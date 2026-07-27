from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest


WORKSPACE = Path(__file__).resolve().parents[3]
CAMPAIGN_MODULE_PATH = WORKSPACE / "scripts" / "vnext_canary_campaign.py"
CAMPAIGN_MODULE_NAME = "vnext_canary_campaign_state_test_module"
TIMESTAMP = "2026-07-27T08:00:00Z"


def _load_campaign_module():
    existing = sys.modules.get(CAMPAIGN_MODULE_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        CAMPAIGN_MODULE_NAME,
        CAMPAIGN_MODULE_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


campaign = _load_campaign_module()


def _matrix() -> list[dict[str, Any]]:
    return [
        {
            "case": "click-seed-101",
            "family": "click_double",
            "runtime_order": ["legacy", "vnext"],
            "scenario": "click_double",
            "seed": 101,
        },
        {
            "case": "click-seed-202",
            "family": "click_double",
            "runtime_order": ["vnext", "legacy"],
            "scenario": "click_double",
            "seed": 202,
        },
        {
            "case": "scroll-seed-303",
            "family": "scroll_roundtrip",
            "runtime_order": ["legacy", "vnext"],
            "scenario": "scroll_roundtrip",
            "seed": 303,
        },
        {
            "case": "scroll-seed-404",
            "family": "scroll_roundtrip",
            "runtime_order": ["vnext", "legacy"],
            "scenario": "scroll_roundtrip",
            "seed": 404,
        },
        {
            "case": "type-seed-505",
            "family": "type_hotkey",
            "runtime_order": ["legacy", "vnext"],
            "scenario": "type_hotkey",
            "seed": 505,
        },
        {
            "case": "type-seed-606",
            "family": "type_hotkey",
            "runtime_order": ["vnext", "legacy"],
            "scenario": "type_hotkey",
            "seed": 606,
        },
    ]


def _manifest_kwargs() -> dict[str, Any]:
    c4_commit = "4" * 40
    model_config = campaign.redacted_config_contract(
        {
            "api": {
                "provider": "openai",
                "model": "visual-model",
                "adapter": "OpenAICompatibleAdapter",
                "temperature": 0,
                "max_tokens": 1024,
                "max_retries": 0,
                "request_timeout_s": 30,
            },
            "vlm": {
                "provider": "openai",
                "model": "visual-model",
            },
            "vnext": {
                "live_canary_enabled": True,
                "max_turns": 8,
                "max_runtime_s": 60,
                "max_model_calls": 8,
            },
        }
    )
    return {
        "campaign_id": "issue31-campaign-state-test",
        "created_at": TIMESTAMP,
        "stack": {
            "master_commit": "1" * 40,
            "c1": {
                "base": "master",
                "head": "2" * 40,
                "pr": 27,
            },
            "c2": {
                "base": "refactor/vnext-live-runtime-core",
                "head": campaign.C2_HEAD,
                "pr": 28,
            },
            "c3": {
                "base": "refactor/vnext-atomic-desktop-adapter",
                "head": "3" * 40,
                "pr": 29,
            },
            "c4": {
                "base": "refactor/vnext-live-model-roles",
                "commit": c4_commit,
                "merge_ref": "5" * 40,
                "pr_head": c4_commit,
            },
        },
        "source_identity": {
            "harness_code_commit": c4_commit,
            "files": {
                "scripts/client_vnext_canary_e2e_run.py": "a" * 64,
                "scripts/vnext_canary_campaign.py": "b" * 64,
            },
        },
        "build_identity": {
            "electron": "c" * 64,
            "main": "d" * 64,
            "package_lock": "e" * 64,
            "renderer": "f" * 64,
            "runtime": {
                "expected_current_commit": c4_commit,
                "expected_loaded_commit": c4_commit,
            },
        },
        "model_config": model_config,
        "matrix": _matrix(),
        "task": "Perform the predeclared action and satisfy the full goal.",
        "target": {
            "target_binding_id": "prevalidated-target",
            "target_hwnd": 100,
            "root_hwnd": 100,
            "process_id": 200,
        },
        "runtime_budget": {
            "max_turns": 8,
            "max_runtime_s": 60,
            "max_model_calls": 8,
        },
        "oracle_version": "external-dom-and-input-oracle/1",
        "c2_unknown_no_replay": {
            "actions_run_id": "30237790573",
            "c2_head": campaign.C2_HEAD,
            "code_sha256": "6" * 64,
            "tests_sha256": "7" * 64,
            "test_nodes": sorted(
                [
                    "test_unknown_raw_result_is_never_replayed",
                    "test_wait_exception_after_completed_input_is_unknown_without_replay",
                ]
            ),
        },
    }


@pytest.fixture
def manifest_kwargs() -> dict[str, Any]:
    return _manifest_kwargs()


@pytest.fixture
def manifest(manifest_kwargs: dict[str, Any]) -> dict[str, Any]:
    return campaign.build_manifest(**manifest_kwargs)


@pytest.fixture
def sealed_campaign(
    tmp_path: Path,
    manifest: dict[str, Any],
) -> tuple[Path, Any]:
    root = tmp_path / "campaign"
    campaign.seal_manifest(root, manifest)
    journal = campaign.CampaignJournal.create(
        root / campaign.JOURNAL_FILENAME,
        manifest,
        timestamp=TIMESTAMP,
    )
    return root, journal


def _invalid_data(
    *,
    attempt: int = 1,
    reason: str = "external_user_input_pre_mutation",
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "reason": reason,
        "external_evidence": True,
        "pre_mutation": True,
        "first_trusted_mutation_observed": False,
        "artifacts": [],
    }


def _failed_data(*, attempt: int = 1) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "reason": "ordinary_model_or_oracle_failure",
        "artifacts": [],
    }


def _break_stack(kwargs: dict[str, Any]) -> None:
    kwargs["stack"]["c4"]["pr_head"] = "9" * 40


def _break_source(kwargs: dict[str, Any]) -> None:
    kwargs["source_identity"]["files"] = {}


def _break_build(kwargs: dict[str, Any]) -> None:
    kwargs["build_identity"]["runtime"]["expected_loaded_commit"] = "9" * 40


def _break_model(kwargs: dict[str, Any]) -> None:
    kwargs["model_config"]["api_max_retries"] = 1


def _break_c2_binding(kwargs: dict[str, Any]) -> None:
    kwargs["c2_unknown_no_replay"]["c2_head"] = "9" * 40


def test_manifest_is_canonical_self_hashed_exclusive_and_round_trips(
    tmp_path: Path,
    manifest: dict[str, Any],
) -> None:
    rebuilt = campaign.build_manifest(**_manifest_kwargs())
    assert rebuilt == manifest
    assert manifest["content_sha256"] == campaign.content_sha256(manifest)
    encoded = campaign.canonical_json_bytes(manifest)
    assert encoded.endswith(b"\n")
    assert not encoded.endswith(b"\n\n")
    assert encoded == campaign.canonical_json_bytes(rebuilt)

    root = tmp_path / "sealed"
    manifest_path = campaign.seal_manifest(root, manifest)
    assert manifest_path.read_bytes() == encoded
    assert campaign.load_manifest(root) == manifest
    with pytest.raises(campaign.CampaignAlreadyExistsError):
        campaign.seal_manifest(root, manifest)


def test_manifest_tamper_is_rejected(
    tmp_path: Path,
    manifest: dict[str, Any],
) -> None:
    root = tmp_path / "sealed"
    manifest_path = campaign.seal_manifest(root, manifest)
    tampered = copy.deepcopy(manifest)
    tampered["samples"][0]["task"] = "changed after sealing"
    manifest_path.write_bytes(campaign.canonical_json_bytes(tampered))

    with pytest.raises(campaign.CampaignIntegrityError):
        campaign.load_manifest(root)


@pytest.mark.parametrize(
    "breaker",
    [
        _break_stack,
        _break_source,
        _break_build,
        _break_model,
        _break_c2_binding,
    ],
    ids=["stack", "source", "build", "model", "c2-evidence"],
)
def test_manifest_rejects_missing_or_inconsistent_required_facts_before_processes(
    tmp_path: Path,
    manifest_kwargs: dict[str, Any],
    breaker: Callable[[dict[str, Any]], None],
) -> None:
    broken = copy.deepcopy(manifest_kwargs)
    breaker(broken)
    campaign_root = tmp_path / "must-not-exist"
    launched: list[str] = []

    with pytest.raises(campaign.CampaignStateError):
        value = campaign.build_manifest(**broken)
        campaign.seal_manifest(campaign_root, value)
        launched.append("electron")

    assert launched == []
    assert not campaign_root.exists()


def test_manifest_expands_exact_fixed_twelve_sample_matrix(
    manifest: dict[str, Any],
) -> None:
    samples = manifest["samples"]
    assert len(samples) == campaign.REQUIRED_SAMPLE_COUNT == 12
    assert [sample["execution_order"] for sample in samples] == list(
        range(1, 13)
    )
    assert {sample["runtime"] for sample in samples} == {"legacy", "vnext"}
    for family in campaign.FAMILIES:
        family_samples = [
            sample for sample in samples if sample["family"] == family
        ]
        assert len(family_samples) == 4
        assert len({sample["seed"] for sample in family_samples}) == 2
        for seed in {sample["seed"] for sample in family_samples}:
            paired = [
                sample
                for sample in family_samples
                if sample["seed"] == seed
            ]
            assert [sample["runtime"] for sample in paired] in (
                ["legacy", "vnext"],
                ["vnext", "legacy"],
            )


def _event_digest(event: dict[str, Any]) -> str:
    material = {
        key: value for key, value in event.items() if key != "event_hash"
    }
    return hashlib.sha256(
        campaign.canonical_json_bytes(material)
    ).hexdigest()


def test_journal_sequences_and_hashes_every_event(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    _, journal = sealed_campaign
    first = manifest["samples"][0]
    started = journal.append(
        sample_key=first["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
        timestamp="2026-07-27T08:00:01Z",
    )
    failed = journal.append(
        sample_key=first["sample_key"],
        kind="sample_failed",
        data=_failed_data(),
        timestamp="2026-07-27T08:00:02Z",
    )
    events = journal.load()

    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert events[0]["previous_event_hash"] == campaign.ZERO_EVENT_HASH
    assert started["previous_event_hash"] == events[0]["event_hash"]
    assert failed["previous_event_hash"] == started["event_hash"]
    assert all(event["event_hash"] == _event_digest(event) for event in events)


def test_journal_append_flushes_before_fsync(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, journal = sealed_campaign
    observations: list[bytes] = []

    def observe_fsync(_descriptor: int) -> None:
        observations.append(journal.path.read_bytes())

    monkeypatch.setattr(campaign.os, "fsync", observe_fsync)
    journal.append(
        sample_key=manifest["samples"][0]["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
        timestamp="2026-07-27T08:00:01Z",
    )

    assert len(observations) == 1
    assert observations[0] == journal.path.read_bytes()
    assert observations[0].count(b"\n") == 2


def _corrupt_truncate(lines: list[bytes]) -> bytes:
    return b"".join(lines)[:-1]


def _corrupt_rewrite(lines: list[bytes]) -> bytes:
    rewritten = json.loads(lines[-1].decode("utf-8"))
    rewritten["timestamp"] = "2026-07-27T09:59:59Z"
    return b"".join([*lines[:-1], campaign.canonical_json_bytes(rewritten)])


def _corrupt_delete(lines: list[bytes]) -> bytes:
    return b"".join([lines[0], *lines[2:]])


def _corrupt_insert(lines: list[bytes]) -> bytes:
    return b"".join([lines[0], b"{}\n", *lines[1:]])


def _corrupt_duplicate(lines: list[bytes]) -> bytes:
    return b"".join([lines[0], lines[1], lines[1], *lines[2:]])


def _corrupt_reorder(lines: list[bytes]) -> bytes:
    return b"".join([lines[0], lines[2], lines[1]])


@pytest.mark.parametrize(
    "corrupt",
    [
        _corrupt_truncate,
        _corrupt_rewrite,
        _corrupt_delete,
        _corrupt_insert,
        _corrupt_duplicate,
        _corrupt_reorder,
    ],
    ids=["truncate", "rewrite", "delete", "insert", "duplicate", "reorder"],
)
def test_journal_rejects_any_chain_corruption(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
    corrupt: Callable[[list[bytes]], bytes],
) -> None:
    _, journal = sealed_campaign
    sample = manifest["samples"][0]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_failed",
        data=_failed_data(),
    )
    lines = journal.path.read_bytes().splitlines(keepends=True)
    journal.path.write_bytes(corrupt(lines))

    with pytest.raises(campaign.CampaignIntegrityError):
        campaign.load_journal_events(journal.path, manifest)


@pytest.mark.parametrize(
    "operation",
    [
        "terminal-without-start",
        "second-sample-first",
        "attempt-two-without-authorization",
        "duplicate-campaign-start",
        "finish-with-unrun-samples",
    ],
)
def test_journal_rejects_illegal_transitions_without_appending(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
    operation: str,
) -> None:
    _, journal = sealed_campaign
    first, second = manifest["samples"][:2]
    before = journal.path.read_bytes()

    with pytest.raises(campaign.CampaignTransitionError):
        if operation == "terminal-without-start":
            journal.append(
                sample_key=first["sample_key"],
                kind="sample_failed",
                data=_failed_data(),
            )
        elif operation == "second-sample-first":
            journal.append(
                sample_key=second["sample_key"],
                kind="sample_started",
                data={"attempt": 1},
            )
        elif operation == "attempt-two-without-authorization":
            journal.append(
                sample_key=first["sample_key"],
                kind="sample_started",
                data={"attempt": 2},
            )
        elif operation == "duplicate-campaign-start":
            journal.append(
                sample_key=campaign.CAMPAIGN_SAMPLE_KEY,
                kind="campaign_started",
                data={},
            )
        else:
            journal.append(
                sample_key=campaign.CAMPAIGN_SAMPLE_KEY,
                kind="campaign_finished",
                data={},
            )

    assert journal.path.read_bytes() == before


def test_ordinary_failures_and_invalid_samples_do_not_short_circuit_matrix(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    _, journal = sealed_campaign
    expected: dict[str, str] = {}
    for index, sample in enumerate(manifest["samples"]):
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_started",
            data={"attempt": 1},
        )
        if index % 2:
            kind = "sample_invalid"
            data = _invalid_data()
            expected[sample["sample_key"]] = "invalid"
        else:
            kind = "sample_failed"
            data = _failed_data()
            expected[sample["sample_key"]] = "failed"
        journal.append(
            sample_key=sample["sample_key"],
            kind=kind,
            data=data,
        )
    journal.append(
        sample_key=campaign.CAMPAIGN_SAMPLE_KEY,
        kind="campaign_finished",
        data={},
    )

    state = journal.state()
    assert state["campaign_finished"] is True
    assert state["campaign_status"] == "failed"
    assert state["event_count"] == 26
    assert {
        key: value["status"] for key, value in state["samples"].items()
    } == expected


def _append_first_invalid(journal: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    sample = manifest["samples"][0]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    return journal.append(
        sample_key=sample["sample_key"],
        kind="sample_invalid",
        data=_invalid_data(),
    )


def _authorize_rerun(
    journal: Any,
    sample_key: str,
    invalid_event: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return journal.append(
        sample_key=sample_key,
        kind="sample_rerun_authorized",
        data={
            "invalid_event_sequence": invalid_event["sequence"],
            "invalid_event_hash": invalid_event["event_hash"],
            **extra,
        },
    )


def test_exactly_one_allowlisted_pre_mutation_invalid_rerun_is_allowed(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    _, journal = sealed_campaign
    first, second = manifest["samples"][:2]
    invalid = _append_first_invalid(journal, manifest)
    _authorize_rerun(journal, first["sample_key"], invalid)
    journal.append(
        sample_key=first["sample_key"],
        kind="sample_started",
        data={"attempt": 2},
    )
    journal.append(
        sample_key=first["sample_key"],
        kind="sample_failed",
        data=_failed_data(attempt=2),
    )
    journal.append(
        sample_key=second["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )

    first_state = journal.state()["samples"][first["sample_key"]]
    assert [attempt["attempt"] for attempt in first_state["attempts"]] == [1, 2]
    assert first_state["rerun_used"] is True
    assert first_state["status"] == "failed"
    assert manifest["samples"][0]["seed"] == first["seed"]


@pytest.mark.parametrize(
    ("reason", "external_evidence", "pre_mutation", "mutation_observed"),
    [
        ("model_strategy_failure", True, True, False),
        ("external_user_input_pre_mutation", False, True, False),
        ("external_user_input_pre_mutation", True, False, False),
        ("external_user_input_pre_mutation", True, True, True),
    ],
    ids=[
        "reason-not-allowlisted",
        "no-external-evidence",
        "not-pre-mutation",
        "mutation-already-observed",
    ],
)
def test_non_objective_or_post_mutation_invalid_verdict_is_rejected(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
    reason: str,
    external_evidence: bool,
    pre_mutation: bool,
    mutation_observed: bool,
) -> None:
    _, journal = sealed_campaign
    sample = manifest["samples"][0]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    data = _invalid_data(reason=reason)
    data.update(
        {
            "external_evidence": external_evidence,
            "pre_mutation": pre_mutation,
            "first_trusted_mutation_observed": mutation_observed,
        }
    )

    with pytest.raises(campaign.CampaignTransitionError):
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_invalid",
            data=data,
        )


def test_failed_sample_cannot_be_reclassified_for_rerun(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    _, journal = sealed_campaign
    sample = manifest["samples"][0]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    failed = journal.append(
        sample_key=sample["sample_key"],
        kind="sample_failed",
        data=_failed_data(),
    )

    with pytest.raises(campaign.CampaignTransitionError):
        _authorize_rerun(journal, sample["sample_key"], failed)


def test_second_rerun_is_rejected(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    _, journal = sealed_campaign
    sample = manifest["samples"][0]
    invalid = _append_first_invalid(journal, manifest)
    _authorize_rerun(journal, sample["sample_key"], invalid)
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 2},
    )
    second_invalid = journal.append(
        sample_key=sample["sample_key"],
        kind="sample_invalid",
        data=_invalid_data(attempt=2),
    )

    with pytest.raises(campaign.CampaignTransitionError):
        _authorize_rerun(journal, sample["sample_key"], second_invalid)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commit", "9" * 40),
        ("build", {"sha256": "9" * 64}),
        ("config", {"temperature": 1}),
        ("task", "changed task"),
        ("seed", 999),
        ("runtime_budget", {"max_turns": 999}),
        ("oracle", {"version": "changed"}),
    ],
)
def test_rerun_identity_drift_claim_is_rejected(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
    field: str,
    value: Any,
) -> None:
    _, journal = sealed_campaign
    sample = manifest["samples"][0]
    invalid = _append_first_invalid(journal, manifest)

    with pytest.raises(campaign.CampaignIntegrityError):
        _authorize_rerun(
            journal,
            sample["sample_key"],
            invalid,
            observed_identity={field: value},
        )


def test_safety_abort_marks_every_remaining_sample_and_is_terminal(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    first, running, *remaining = manifest["samples"]
    journal.append(
        sample_key=first["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    journal.append(
        sample_key=first["sample_key"],
        kind="sample_failed",
        data=_failed_data(),
    )
    journal.append(
        sample_key=running["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    abort_event = journal.append(
        sample_key=running["sample_key"],
        kind="safety_abort",
        data={"reason": "unauthorized_desktop_input"},
    )
    with pytest.raises(campaign.CampaignTransitionError):
        journal.append(
            sample_key=remaining[0]["sample_key"],
            kind="sample_started",
            data={"attempt": 1},
        )
    journal.append(
        sample_key=campaign.CAMPAIGN_SAMPLE_KEY,
        kind="campaign_finished",
        data={},
    )

    state = journal.state()
    assert state["abort_event"]["event_hash"] == abort_event["event_hash"]
    assert state["samples"][first["sample_key"]]["status"] == "failed"
    assert state["samples"][running["sample_key"]]["status"] == "safety_aborted"
    assert {
        state["samples"][sample["sample_key"]]["status"]
        for sample in remaining
    } == {"not_run_due_to_safety_abort"}
    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    assert [
        item["report_status"] for item in report["samples"][2:]
    ] == ["not_run_due_to_safety_abort"] * len(remaining)


def _click_evidence(
    *,
    decision_id: str = "decision-1",
    run_id: str = "run-1",
    obligation_id: str = "obligation-1",
    target_identity: dict[str, Any] | None = None,
    outcome: str = "APPLIED",
) -> dict[str, Any]:
    target = target_identity or {
        "target_binding_id": "prevalidated-target",
        "target_hwnd": 100,
        "root_hwnd": 100,
        "process_id": 200,
    }

    def bound(**extra: Any) -> dict[str, Any]:
        return {
            "decision_id": decision_id,
            "run_id": run_id,
            "obligation_id": obligation_id,
            **target,
            **extra,
        }

    queue_start = bound(
        action="click",
        point=[10, 20],
        timestamp=1.0,
        sequence=1,
        mechanism="RawDeviceExecutor",
    )
    queue_done = bound(
        action="click",
        timestamp=2.0,
        sequence=2,
        mechanism="RawDeviceExecutor",
    )
    receipt = bound(
        action="click",
        timestamp=1.8,
        outcome=outcome,
        mechanism="RawDeviceExecutor",
    )
    turn = bound(
        action="click",
        status="mission_verified",
        action_effect={
            "action_id": decision_id,
            "local_visual_change": "changed",
        },
    )
    browser = [
        bound(
            type=event_type,
            trusted=True,
            timestamp=timestamp,
            sequence=index,
            screen_x=10,
            screen_y=20,
            actual_changed=True,
        )
        for index, (event_type, timestamp) in enumerate(
            [
                ("pointerdown", 1.2),
                ("pointerup", 1.3),
                ("click", 1.4),
            ],
            start=1,
        )
    ]
    browser[0]["dom_before"] = {
        "click_count": 0,
        "target_state": "ready",
    }
    browser[-1]["dom_after"] = {
        "click_count": 1,
        "target_state": "complete",
    }
    browser.append(
        bound(
            type="scenario_success",
            trusted=True,
            timestamp=1.5,
            sequence=4,
        )
    )
    os_events = [
        bound(
            type=event_type,
            trusted=True,
            injected=True,
            timestamp=timestamp,
            sequence=index,
            mechanism="RawDeviceExecutor",
        )
        for index, (event_type, timestamp) in enumerate(
            [
                ("pointerdown", 1.2),
                ("pointerup", 1.3),
                ("click", 1.4),
            ],
            start=1,
        )
    ]
    return {
        "run_id": run_id,
        "obligation_id": obligation_id,
        "target_identity": target,
        "queue_start_events": [queue_start],
        "queue_done_events": [queue_done],
        "receipt_events": [receipt],
        "turn_events": [turn],
        "browser_events": browser,
        "os_events": os_events,
    }


def test_action_evidence_join_accepts_one_unique_cross_source_action() -> None:
    result = campaign.reconcile_action_evidence(**_click_evidence())

    assert result["passed"] is True
    assert result["errors"] == []
    assert result["summary"] == {
        "action_count": 1,
        "action_effect_disagreement_count": 0,
        "evidence_join_ambiguous_count": 0,
        "false_done_count": 0,
        "unknown_observed_count": 0,
        "unknown_replay_count": 0,
        "identity_admission_bypass_count": 0,
        "legacy_fallback_count": 0,
    }
    assert result["actions"][0]["backend_attempt_count"] == 1
    assert result["actions"][0]["raw_receipt_count"] == 1
    assert result["actions"][0]["trusted_input_group_count"] == 1
    assert result["actions"][0]["actual_changed"] is True
    assert result["actions"][0]["action_effect"] == {
        "action_id": "decision-1",
        "local_visual_change": "changed",
    }


def test_action_evidence_join_rejects_overlapping_ambiguous_actions() -> None:
    first = _click_evidence(decision_id="decision-1")
    second = _click_evidence(decision_id="decision-2")
    combined: dict[str, Any] = {
        "run_id": first["run_id"],
        "obligation_id": first["obligation_id"],
        "target_identity": first["target_identity"],
    }
    for name in (
        "queue_start_events",
        "queue_done_events",
        "receipt_events",
        "turn_events",
    ):
        combined[name] = [*first[name], *second[name]]
    combined["browser_events"] = copy.deepcopy(first["browser_events"])
    combined["os_events"] = copy.deepcopy(first["os_events"])
    for event in [*combined["browser_events"], *combined["os_events"]]:
        event.pop("decision_id", None)

    result = campaign.reconcile_action_evidence(**combined)

    assert result["passed"] is False
    assert result["summary"]["evidence_join_ambiguous_count"] > 0
    assert {
        error["code"] for error in result["errors"]
    } >= {"evidence_join_ambiguous"}


def test_action_evidence_join_rejects_target_identity_mismatch() -> None:
    evidence = _click_evidence()
    evidence["receipt_events"][0]["target_binding_id"] = "different-target"

    result = campaign.reconcile_action_evidence(**evidence)

    assert result["passed"] is False
    assert result["summary"]["identity_admission_bypass_count"] == 1
    assert result["safety_abort_reasons"] == ["identity_admission_bypass"]


def test_unknown_action_is_counted_once_without_replay() -> None:
    result = campaign.reconcile_action_evidence(
        **_click_evidence(outcome="OUTCOME_UNKNOWN")
    )

    assert result["passed"] is True
    assert result["summary"]["unknown_observed_count"] == 1
    assert result["summary"]["unknown_replay_count"] == 0
    assert result["actions"][0]["backend_attempt_count"] == 1
    assert result["actions"][0]["raw_receipt_count"] == 1
    assert result["actions"][0]["replay_detected"] is False


def test_unknown_action_duplicate_backend_attempt_is_safety_abort() -> None:
    evidence = _click_evidence(outcome="OUTCOME_UNKNOWN")
    evidence["queue_start_events"].append(
        copy.deepcopy(evidence["queue_start_events"][0])
    )

    result = campaign.reconcile_action_evidence(**evidence)

    assert result["passed"] is False
    assert result["summary"]["unknown_observed_count"] >= 1
    assert result["summary"]["unknown_replay_count"] >= 1
    assert "unknown_mutation_replay" in result["safety_abort_reasons"]


def _write_action_reconciliation(
    root: Path,
    sample: dict[str, Any],
) -> dict[str, Any]:
    run_id = f"run-{sample['execution_order']:03d}"
    obligation_id = f"obligation-{sample['execution_order']:03d}"
    decision_id = f"decision-{sample['execution_order']:03d}"
    evidence = _click_evidence(
        decision_id=decision_id,
        run_id=run_id,
        obligation_id=obligation_id,
        target_identity=sample["target"],
    )
    reconciliation = campaign.reconcile_action_evidence(**evidence)
    assert reconciliation["passed"] is True
    return campaign.write_immutable_artifact(
        root,
        (
            f"artifacts/{sample['execution_order']:03d}/"
            "action-reconciliation.json"
        ),
        reconciliation,
        kind="action_reconciliation",
    )


def _complete_passing_campaign(
    root: Path,
    journal: Any,
    manifest: dict[str, Any],
) -> None:
    for sample in manifest["samples"]:
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_started",
            data={"attempt": 1},
        )
        artifacts = (
            [_write_action_reconciliation(root, sample)]
            if sample["runtime"] == "vnext"
            else []
        )
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_passed",
            data={"attempt": 1, "artifacts": artifacts},
        )
    journal.append(
        sample_key=campaign.CAMPAIGN_SAMPLE_KEY,
        kind="campaign_finished",
        data={},
    )


def test_report_is_stable_complete_and_binds_c2_fault_injection(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    _complete_passing_campaign(root, journal, manifest)

    first = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    second = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )

    assert first == second
    assert campaign.canonical_json_bytes(first) == campaign.canonical_json_bytes(
        second
    )
    assert first["passed"] is True
    assert first["missing_sample_keys"] == []
    assert len(first["samples"]) == 12
    assert [item["sample_key"] for item in first["samples"]] == [
        sample["sample_key"] for sample in manifest["samples"]
    ]
    assert first["unknown_supplemental_evidence"] == (
        manifest["c2_unknown_no_replay"]
    )
    assert (
        first["unknown_supplemental_evidence"]["c2_head"]
        == campaign.C2_HEAD
    )
    assert campaign.validate_campaign_report(first) == first


def test_missing_samples_fail_report_closed(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign

    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )

    assert report["passed"] is False
    assert report["missing_sample_keys"] == [
        sample["sample_key"] for sample in manifest["samples"]
    ]
    assert report["sample_status_counts"] == {"failed": 12}
    assert all(
        "missing_declared_sample" in sample["errors"]
        for sample in report["samples"]
    )


def test_manifest_external_fact_injection_is_rejected_before_report(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    _, journal = sealed_campaign
    sample = manifest["samples"][0]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    injected = _failed_data()
    injected["config"] = {"temperature": 1}
    before = journal.path.read_bytes()

    with pytest.raises(campaign.CampaignIntegrityError):
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_failed",
            data=injected,
        )

    assert journal.path.read_bytes() == before


def test_report_and_artifacts_are_immutable_no_overwrite_objects(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    artifact = campaign.write_immutable_artifact(
        root,
        "artifacts/immutable.json",
        {"fact": "original"},
        kind="raw_evidence",
    )
    with pytest.raises(campaign.CampaignAlreadyExistsError):
        campaign.write_immutable_artifact(
            root,
            artifact["path"],
            {"fact": "replacement"},
            kind="raw_evidence",
        )

    _complete_passing_campaign(root, journal, manifest)
    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    first_write = campaign.seal_campaign_report(root, report)
    read_only_repeat = campaign.seal_campaign_report(root, report)
    assert first_write["created"] is True
    assert read_only_repeat["created"] is False

    changed = copy.deepcopy(report)
    changed.pop("content_sha256")
    changed["passed"] = False
    changed = campaign.with_content_hash(changed)
    with pytest.raises(campaign.CampaignAlreadyExistsError):
        campaign.seal_campaign_report(root, changed)


def test_sealed_report_tamper_is_rejected(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    _complete_passing_campaign(root, journal, manifest)
    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    campaign.seal_campaign_report(root, report)
    report_path = root / campaign.REPORT_FILENAME
    tampered = copy.deepcopy(report)
    tampered["passed"] = False
    report_path.write_bytes(campaign.canonical_json_bytes(tampered))

    with pytest.raises(campaign.CampaignIntegrityError):
        campaign.load_campaign_report(root)


def test_referenced_artifact_tamper_fails_report_closed(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    _complete_passing_campaign(root, journal, manifest)
    original = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    referenced = next(
        sample["attempts"][0]["artifacts"][0]["path"]
        for sample in original["samples"]
        if sample["runtime"] == "vnext"
    )
    (root / Path(*referenced.split("/"))).write_bytes(b"tampered\n")

    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )

    assert report["passed"] is False
    affected = next(
        sample
        for sample in report["samples"]
        if any(
            error.startswith("artifact_integrity:")
            for error in sample["errors"]
        )
    )
    assert "missing_or_duplicate_action_reconciliation" in affected["errors"]


def test_unreferenced_artifact_cannot_inject_report_facts(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    _complete_passing_campaign(root, journal, manifest)
    before = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    marker = "manifest-external-injected-fact"
    campaign.write_immutable_artifact(
        root,
        "artifacts/unreferenced.json",
        {"marker": marker},
        kind="raw_evidence",
    )
    after = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )

    assert after == before
    assert marker.encode("utf-8") not in campaign.canonical_json_bytes(after)
