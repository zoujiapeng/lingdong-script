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
                "live_agent_enabled": True,
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
            "evidence_plane": {
                "base": "refactor/vnext-live-model-roles",
                "head": "8" * 40,
                "pr": 32,
            },
            "evidence_persistence": {
                "base": "refactor/vnext-runtime-evidence-plane",
                "head": "9" * 40,
                "pr": 34,
            },
            "c4": {
                "base": "refactor/vnext-runtime-evidence-persistence",
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
        "oracle_version": "independent-vnext-canary-browser-os-join/2",
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
    root: Path,
    sample: dict[str, Any],
    *,
    attempt: int = 1,
    reason: str = "external_user_input_pre_mutation",
) -> dict[str, Any]:
    prefix = (
        f"artifacts/{sample['execution_order']:03d}/"
        f"invalid-attempt-{attempt}"
    )
    external = _safe_external_oracle(
        sample,
        passed=False,
        attempt=attempt,
    )
    external_identity = campaign.write_immutable_artifact(
        root,
        f"{prefix}-external-oracle.json",
        external,
        kind="external_oracle",
    )
    os_evidence = {
        "format": "vnext-live-canary-os-input-safe/2",
        "hook_sequence": 1,
        "raw_event_count": 1,
        "raw_event_stream_sha256": "1" * 64,
        "trusted_action_event_count": 0,
        "trusted_action_event_stream_sha256": "2" * 64,
        "event_sequences": [],
        "event_type_counts": {},
        "action_counts": {},
        "decision_ids": [],
        "external_untrusted_mutation_count": 1,
        "external_untrusted_target_mutation_count": 0,
        "unexpected_injected_target_mutation_count": 0,
        "dropped_event_count": 0,
        "thread_error_present": False,
        "input_release_audit": {
            "passed": True,
            "unbalanced_key_count": 0,
            "unbalanced_button_count": 0,
            "error_identity": {"count": 0, "sha256": "3" * 64},
        },
    }
    os_identity = campaign.write_immutable_artifact(
        root,
        f"{prefix}-os-input.json",
        os_evidence,
        kind="os_input_evidence",
    )
    invalid_identity = campaign.write_immutable_artifact(
        root,
        f"{prefix}-proof.json",
        {
            "format": "vnext-live-canary-invalid-run-evidence/1",
            "reason": reason,
            "pre_mutation": True,
            "first_trusted_mutation_observed": False,
            "external_oracle_sha256": external_identity["sha256"],
            "os_input_evidence_sha256": os_identity["sha256"],
            "trusted_action_event_count": 0,
            "external_untrusted_mutation_count": 1,
            "dropped_event_count": 0,
            "thread_error_present": False,
        },
        kind=campaign.INVALID_RUN_EVIDENCE_KIND,
    )
    return {
        "attempt": attempt,
        "reason": reason,
        "external_evidence": True,
        "pre_mutation": True,
        "first_trusted_mutation_observed": False,
        "artifacts": [
            external_identity,
            os_identity,
            invalid_identity,
        ],
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
    root, journal = sealed_campaign
    expected: dict[str, str] = {}
    for index, sample in enumerate(manifest["samples"]):
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_started",
            data={"attempt": 1},
        )
        if index % 2:
            kind = "sample_invalid"
            data = _invalid_data(root, sample)
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


def _append_first_invalid(
    root: Path,
    journal: Any,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    sample = manifest["samples"][0]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    return journal.append(
        sample_key=sample["sample_key"],
        kind="sample_invalid",
        data=_invalid_data(root, sample),
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
    root, journal = sealed_campaign
    first, second = manifest["samples"][:2]
    invalid = _append_first_invalid(root, journal, manifest)
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
    root, journal = sealed_campaign
    sample = manifest["samples"][0]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    data = _invalid_data(root, sample, reason=reason)
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


def test_invalid_verdict_rejects_empty_external_evidence_artifacts(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    sample = manifest["samples"][0]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    data = _invalid_data(root, sample)
    data["artifacts"] = []

    with pytest.raises(
        campaign.CampaignTransitionError,
        match="invalid-run evidence artifact",
    ):
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_invalid",
            data=data,
        )


def test_invalid_verdict_rejects_nonexistent_evidence_before_journal_append(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    sample = manifest["samples"][0]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    data = _invalid_data(root, sample)
    invalid_index = next(
        index
        for index, identity in enumerate(data["artifacts"])
        if identity.get("kind") == campaign.INVALID_RUN_EVIDENCE_KIND
    )
    data["artifacts"][invalid_index] = {
        "path": "artifacts/nonexistent-invalid-proof.json",
        "sha256": "0" * 64,
        "size": 1,
        "kind": campaign.INVALID_RUN_EVIDENCE_KIND,
    }
    before = journal.path.read_bytes()

    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="artifact is unavailable",
    ):
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_invalid",
            data=data,
        )

    assert journal.path.read_bytes() == before


def test_rerun_revalidates_invalid_artifact_before_second_sample_start(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    sample = manifest["samples"][0]
    invalid = _append_first_invalid(root, journal, manifest)
    _authorize_rerun(
        journal,
        sample["sample_key"],
        invalid,
    )
    invalid_identity = next(
        identity
        for identity in invalid["data"]["artifacts"]
        if identity.get("kind") == campaign.INVALID_RUN_EVIDENCE_KIND
    )
    invalid_path = root / Path(*invalid_identity["path"].split("/"))
    invalid_path.write_bytes(b"tampered\n")
    before = journal.path.read_bytes()

    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="artifact",
    ):
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_started",
            data={"attempt": 2},
        )

    assert journal.path.read_bytes() == before
    assert journal.state()["samples"][sample["sample_key"]]["status"] == (
        "rerun_authorized"
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
    root, journal = sealed_campaign
    sample = manifest["samples"][0]
    invalid = _append_first_invalid(root, journal, manifest)
    _authorize_rerun(journal, sample["sample_key"], invalid)
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 2},
    )
    second_invalid = journal.append(
        sample_key=sample["sample_key"],
        kind="sample_invalid",
        data=_invalid_data(root, sample, attempt=2),
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
    root, journal = sealed_campaign
    sample = manifest["samples"][0]
    invalid = _append_first_invalid(root, journal, manifest)

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


def _evidence_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _canonical_target(
    target: dict[str, Any],
) -> dict[str, Any]:
    return {
        "binding_id": target["target_binding_id"],
        "hwnd": target["target_hwnd"],
        "process_id": target["process_id"],
        "executable_identity": "opaque-prevalidated-executable",
        "epoch": 4,
    }


def _action_arguments(action: str) -> dict[str, Any]:
    return copy.deepcopy(
        {
            "click": {"image_x": 10.0, "image_y": 20.0},
            "double_click": {"image_x": 10.0, "image_y": 20.0},
            "scroll": {
                "image_x": 10.0,
                "image_y": 20.0,
                "amount": -240,
            },
            "type_text": {"text": "private exact canary text"},
            "hotkey": {"keys": ["ctrl", "l"]},
        }[action]
    )


def _indexed_action_arguments(action: str, index: int) -> dict[str, Any]:
    arguments = _action_arguments(action)
    if action == "scroll" and index % 2 == 0:
        arguments["amount"] = 240
    return arguments


def _physical_arguments(
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if action in {"click", "double_click"}:
        return {
            "kind": "pointer_click",
            "x": 10,
            "y": 20,
            "button": "left",
            "click_count": 2 if action == "double_click" else 1,
        }
    if action == "scroll":
        return {
            "kind": "wheel",
            "x": 10,
            "y": 20,
            "signed_wheel_amount": arguments["amount"],
            "event_wheel_delta": arguments["amount"],
        }
    if action == "type_text":
        return {"kind": "type_text", "text": arguments["text"]}
    return {
        "kind": "hotkey",
        "modifiers": arguments["keys"][:-1],
        "key": arguments["keys"][-1],
    }


def _frame_identity(frame_id: int) -> dict[str, Any]:
    return {
        "frame_id": frame_id,
        "epoch": 4,
        "hwnd": 100,
        "region": [0, 0, 100, 80],
        "scope": "target_window",
        "submitted_size": [100, 80],
        "image_sha256": f"{frame_id:064x}",
        "media_type": "image/jpeg",
    }


def _runtime_report_and_anchor(
    *,
    actions: list[str],
    run_id: str = "run-1",
    obligation_id: str = "obligation-1",
    target: dict[str, Any] | None = None,
    outcomes: dict[int, str] | None = None,
    effect_changes: dict[int, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_target = target or {
        "target_binding_id": "prevalidated-target",
        "target_hwnd": 100,
        "root_hwnd": 100,
        "process_id": 200,
    }
    evidence_target = _canonical_target(expected_target)
    decisions: list[dict[str, Any]] = []
    admissions: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        decision_id = f"decision-{index:03d}"
        arguments = _indexed_action_arguments(action, index)
        envelope = {
            "decision_id": decision_id,
            "proposal": {"action": action, "args": arguments},
        }
        projection = {"projection_id": f"projection-{index:03d}"}
        schema = {"type": "object", "action": action}
        decision = {
            "format": "actor-decision-record/1",
            "decision_id": decision_id,
            "mission_id": "mission-1",
            "run_id": run_id,
            "mission_revision": 0,
            "obligation_id": obligation_id,
            "turn_index": index - 1,
            "proposal_kind": "desktop_action",
            "action": action,
            "canonical_arguments": arguments,
            "canonical_arguments_sha256": _evidence_sha256(arguments),
            "actor_envelope": envelope,
            "actor_envelope_sha256": _evidence_sha256(envelope),
            "broker_projection": projection,
            "broker_projection_sha256": _evidence_sha256(projection),
            "tool_schema": schema,
            "tool_schema_sha256": _evidence_sha256(schema),
        }
        before_identity = _frame_identity(index * 2 - 1)
        after_identity = _frame_identity(index * 2)
        admission = {
            "format": "admission-record/1",
            "decision_id": decision_id,
            "actor_decision_record_sha256": _evidence_sha256(decision),
            "actor_envelope_sha256": decision["actor_envelope_sha256"],
            "frame_identity": before_identity,
            "target_identity": evidence_target,
            "mutation_permit_id": f"permit-{index:03d}",
            "controller_route": "vnext_atomic_desktop_adapter",
            "accepted_timestamp_ns": index * 10,
        }
        reservation = f"{index + 20:064x}"
        attempt = {
            "format": "physical-attempt-record/1",
            "mission_id": "mission-1",
            "run_id": run_id,
            "obligation_id": obligation_id,
            "decision_id": decision_id,
            "action_id": decision_id,
            "action": action,
            "attempt_index": 1,
            "submitted_arguments": arguments,
            "physical_arguments": _physical_arguments(action, arguments),
            "raw_device_route": f"raw_device.{action}/0",
            "mechanism": "physical_sendinput",
            "target_identity": evidence_target,
            "frame_identity": before_identity,
            "mutation_permit_id": admission["mutation_permit_id"],
            "actor_decision_record_sha256": _evidence_sha256(decision),
            "admission_record_sha256": _evidence_sha256(admission),
            "timestamp_ns": index * 10 + 1,
            "reservation_sha256": reservation,
        }
        before_ref = {
            "format": "frame-artifact-reference/1",
            "mission_id": "mission-1",
            "run_id": run_id,
            "decision_id": decision_id,
            "action_id": decision_id,
            "frame_id": before_identity["frame_id"],
            "frame_identity": before_identity,
            "artifact_uri": f"artifacts/{decision_id}-before.jpg",
            "raw_jpeg_sha256": f"{index + 30:064x}",
            "runtime_frame_image_sha256": before_identity["image_sha256"],
            "capture_timestamp_ns": index * 10 - 1,
            "role": "before",
        }
        after_ref = {
            **before_ref,
            "frame_id": after_identity["frame_id"],
            "frame_identity": after_identity,
            "artifact_uri": f"artifacts/{decision_id}-after.jpg",
            "raw_jpeg_sha256": f"{index + 40:064x}",
            "runtime_frame_image_sha256": after_identity["image_sha256"],
            "capture_timestamp_ns": index * 10 + 2,
            "role": "after",
        }
        outcome = (outcomes or {}).get(index, "applied")
        unknown = outcome == "outcome_unknown"
        receipt = {
            "action": "click" if action == "double_click" else action,
            "outcome": outcome,
            "input_completed": None if unknown else True,
        }
        change = (effect_changes or {}).get(index, "changed")
        action_effect = {
            "action_id": decision_id,
            "input_completed": True,
            "before_frame_id": before_identity["frame_id"],
            "after_frame_id": after_identity["frame_id"],
            "before_frame_payload_hash": _evidence_sha256(before_identity),
            "after_frame_payload_hash": _evidence_sha256(after_identity),
            "target_binding_valid": True,
            "frame_comparable": True,
            "local_visual_change": change,
            "change_ratio": 0.25 if change == "changed" else 0.0,
            "reason": "pixel_difference",
            "semantic_completion": "unknown",
            "evidence_ref": f"effect-{index:03d}",
            "payload_hash": f"{index + 50:064x}",
        }
        effect_ref = {
            "id": action_effect["evidence_ref"],
            "payload_hash": action_effect["payload_hash"],
        }
        transition = {
            "action_id": decision_id,
            "action": action,
            "outcome": "unknown" if unknown else "completed",
            "input_completed": None if unknown else True,
            "post_frame_id": None if unknown else after_identity["frame_id"],
            "post_frame_evidence": [],
            "action_effect": None if unknown else action_effect,
            "action_effect_evidence": None if unknown else effect_ref,
        }
        binding = {
            "format": "action-execution-binding/1",
            "action_id": decision_id,
            "decision_id": decision_id,
            "action": action,
            "actor_envelope_sha256": decision["actor_envelope_sha256"],
            "raw_action_receipt": receipt,
            "raw_action_receipt_sha256": _evidence_sha256(receipt),
            "controller_transition": transition,
            "controller_transition_sha256": _evidence_sha256(transition),
            "physical_attempt_count": 1,
            "attempt_reservation_sha256": reservation,
            "physical_attempt_record_sha256": _evidence_sha256(attempt),
            "before_frame_identity": before_identity,
            "after_frame_identity": None if unknown else after_identity,
            "before_artifact_reference_sha256": _evidence_sha256(before_ref),
            "after_artifact_reference_sha256": (
                None if unknown else _evidence_sha256(after_ref)
            ),
        }
        decisions.append(decision)
        admissions.append(admission)
        attempts.append(attempt)
        bindings.append(binding)
        artifacts.append(before_ref)
        if not unknown:
            artifacts.append(after_ref)
    missing_evidence = [
        f"decision-{index:03d}_after_frame_artifact"
        for index in range(1, len(actions) + 1)
        if (outcomes or {}).get(index) == "outcome_unknown"
    ]
    report = {
        "format": campaign.RUNTIME_EVIDENCE_REPORT_FORMAT,
        "mission_id": "mission-1",
        "run_id": run_id,
        "status": "incomplete" if missing_evidence else "complete",
        "actor_decisions": decisions,
        "admissions": admissions,
        "physical_attempts": attempts,
        "action_bindings": bindings,
        "frame_artifacts": artifacts,
        "journal_head_sha256": "a" * 64,
        "journal_event_count": len(actions) * 7,
        "missing_evidence": missing_evidence,
        "verification_failures": [],
    }
    anchor = {
        "type": "runtime_evidence_sealed",
        "ts": 123.456,
        "mission_id": report["mission_id"],
        "run_id": run_id,
        "journal_head_sha256": report["journal_head_sha256"],
        "journal_event_count": report["journal_event_count"],
        "report_sha256": _evidence_sha256(report),
        "report_status": report["status"],
        "missing_evidence_count": len(missing_evidence),
        "verification_failure_count": 0,
        "runtime_version": "vnext",
        "no_legacy_fallback": True,
    }
    return report, anchor


def _external_action_events(
    *,
    action: str,
    decision_id: str,
    arguments: dict[str, Any],
    target: dict[str, Any],
    sequence_base: int,
    actual_changed: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def bound(event_type: str, sequence: int, **extra: Any) -> dict[str, Any]:
        return {
            "type": event_type,
            "decision_id": decision_id,
            "action_id": decision_id,
            "trusted": True,
            "sequence": sequence_base + sequence,
            **target,
            **extra,
        }

    if action == "click":
        browser = [
            bound("pointerdown", 1, screen_x=10, screen_y=20),
            bound("pointerup", 2, screen_x=10, screen_y=20),
            bound("click", 3),
        ]
        os_events = [
            bound("pointerdown", 1, injected=True),
            bound("pointerup", 2, injected=True),
        ]
    elif action == "double_click":
        browser = [
            bound("pointerdown", 1, screen_x=10, screen_y=20),
            bound("pointerup", 2, screen_x=10, screen_y=20),
            bound("click", 3),
            bound("pointerdown", 4, screen_x=10, screen_y=20),
            bound("pointerup", 5, screen_x=10, screen_y=20),
            bound("click", 6),
            bound("dblclick", 7),
        ]
        os_events = [
            bound("pointerdown", 1, injected=True),
            bound("pointerup", 2, injected=True),
            bound("pointerdown", 3, injected=True),
            bound("pointerup", 4, injected=True),
        ]
    elif action == "scroll":
        direction = "up" if arguments["amount"] > 0 else "down"
        delta = -120 if direction == "up" else 120
        browser = [
            bound(
                "wheel",
                1,
                screen_x=10,
                screen_y=20,
                direction=direction,
                delta_y=delta,
            )
        ]
        os_events = [bound("wheel", 1, injected=True)]
    elif action == "type_text":
        browser = [
            bound("beforeinput", 1),
            bound("input", 2, data=arguments["text"]),
        ]
        os_events = [
            bound("keydown", 1, injected=True),
            bound("keyup", 2, injected=True),
        ]
    else:
        keys = arguments["keys"]
        browser = [
            *[
                bound("keydown", index, key=key, repeat=False)
                for index, key in enumerate(keys, start=1)
            ],
            *[
                bound(
                    "keyup",
                    len(keys) + index,
                    key=key,
                    repeat=False,
                )
                for index, key in enumerate(reversed(keys), start=1)
            ],
        ]
        os_events = [
            *[
                bound("keydown", index, injected=True)
                for index, _key in enumerate(keys, start=1)
            ],
            *[
                bound("keyup", len(keys) + index, injected=True)
                for index, _key in enumerate(keys, start=1)
            ],
        ]
    browser[0]["dom_before"] = {"state": "before"}
    browser[-1]["dom_after"] = {
        "state": "after" if actual_changed else "before"
    }
    for event in browser:
        event["actual_changed"] = actual_changed
    browser.append(bound("scenario_success", 99))
    return browser, os_events


def _canonical_evidence(
    *,
    actions: list[str],
    run_id: str = "run-1",
    obligation_id: str = "obligation-1",
    target_identity: dict[str, Any] | None = None,
    outcomes: dict[int, str] | None = None,
    effect_changes: dict[int, str] | None = None,
    external_changes: dict[int, bool] | None = None,
) -> dict[str, Any]:
    target = target_identity or {
        "target_binding_id": "prevalidated-target",
        "target_hwnd": 100,
        "root_hwnd": 100,
        "process_id": 200,
    }
    report, anchor = _runtime_report_and_anchor(
        actions=actions,
        run_id=run_id,
        obligation_id=obligation_id,
        target=target,
        outcomes=outcomes,
        effect_changes=effect_changes,
    )
    browser: list[dict[str, Any]] = []
    os_events: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        action_browser, action_os = _external_action_events(
            action=action,
            decision_id=f"decision-{index:03d}",
            arguments=_indexed_action_arguments(action, index),
            target=target,
            sequence_base=index * 100,
            actual_changed=(external_changes or {}).get(index, True),
        )
        browser.extend(action_browser)
        os_events.extend(action_os)
    return {
        "run_id": run_id,
        "obligation_id": obligation_id,
        "target_identity": target,
        "runtime_terminal_status": "mission_verified",
        "external_oracle_passed": True,
        "runtime_evidence_report": report,
        "runtime_evidence_anchor": anchor,
        "browser_events": browser,
        "os_events": os_events,
    }


@pytest.mark.parametrize(
    "actions",
    [
        ["click", "double_click"],
        ["scroll", "scroll"],
        ["type_text", "hotkey"],
    ],
)
def test_action_evidence_join_uses_real_family_signatures(
    actions: list[str],
) -> None:
    result = campaign.reconcile_action_evidence(
        **_canonical_evidence(actions=actions)
    )

    assert result["passed"] is True
    assert result["errors"] == []
    assert result["summary"]["action_count"] == 2
    assert all(item["physical_attempt_count"] == 1 for item in result["actions"])
    assert all(
        item["trusted_input_group_count"] == 1 for item in result["actions"]
    )


@pytest.mark.parametrize(
    ("canonical_keys", "browser_keys"),
    [
        (["ctrl", "a"], ["Control", "a"]),
        (["ctrl", "enter"], ["Control", "Enter"]),
    ],
)
def test_browser_hotkey_signature_accepts_only_dom_control_alias(
    canonical_keys: list[str],
    browser_keys: list[str],
) -> None:
    events = [
        *[
            {"type": "keydown", "key": key, "repeat": False}
            for key in browser_keys
        ],
        *[
            {"type": "keyup", "key": key, "repeat": False}
            for key in reversed(browser_keys)
        ],
    ]

    assert campaign._browser_signature_errors(
        action="hotkey",
        canonical_arguments={"keys": canonical_keys},
        physical_arguments={},
        events=events,
    ) == []


@pytest.mark.parametrize("invalid_kind", ["order", "cardinality", "repeat"])
def test_browser_hotkey_control_alias_keeps_strict_sequence_checks(
    invalid_kind: str,
) -> None:
    events = [
        {"type": "keydown", "key": "Control", "repeat": False},
        {"type": "keydown", "key": "a", "repeat": False},
        {"type": "keyup", "key": "a", "repeat": False},
        {"type": "keyup", "key": "Control", "repeat": False},
    ]
    if invalid_kind == "order":
        events[0], events[1] = events[1], events[0]
    elif invalid_kind == "cardinality":
        events.insert(
            2,
            {"type": "keydown", "key": "a", "repeat": False},
        )
    else:
        events[1]["repeat"] = True

    assert campaign._browser_signature_errors(
        action="hotkey",
        canonical_arguments={"keys": ["ctrl", "a"]},
        physical_arguments={},
        events=events,
    ) == ["trusted_hotkey_sequence_mismatch"]


def test_action_evidence_join_requires_exact_external_action_ids() -> None:
    evidence = _canonical_evidence(actions=["click", "double_click"])
    for event in [
        *evidence["browser_events"],
        *evidence["os_events"],
    ]:
        event.pop("decision_id", None)
        event.pop("action_id", None)

    result = campaign.reconcile_action_evidence(**evidence)

    assert result["passed"] is False
    assert result["summary"]["evidence_join_ambiguous_count"] == 2
    assert "unauthorized_desktop_input" in {
        error["code"] for error in result["errors"]
    }


def test_action_evidence_join_rejects_target_identity_mismatch() -> None:
    evidence = _canonical_evidence(actions=["click"])
    evidence["target_identity"]["target_binding_id"] = "different-target"

    result = campaign.reconcile_action_evidence(**evidence)

    assert result["passed"] is False
    assert result["summary"]["identity_admission_bypass_count"] == 1
    assert result["safety_abort_reasons"] == ["identity_admission_bypass"]


def test_runtime_evidence_anchor_must_authenticate_rebuilt_report() -> None:
    evidence = _canonical_evidence(actions=["click"])
    evidence["runtime_evidence_anchor"]["report_sha256"] = "f" * 64

    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="seal anchor",
    ):
        campaign.reconcile_action_evidence(**evidence)


def test_reconciliation_safe_projection_omits_private_runtime_payloads() -> None:
    evidence = _canonical_evidence(actions=["type_text", "hotkey"])
    result = campaign.reconcile_action_evidence(**evidence)
    encoded = campaign.canonical_json_bytes(result)

    assert result["passed"] is True
    assert b"private exact canary text" not in encoded
    assert b'"actor_envelope"' not in encoded
    assert b'"canonical_arguments"' not in encoded
    assert b'"raw_action_receipt"' not in encoded
    assert b'"controller_transition"' not in encoded


def test_runtime_done_without_external_oracle_pass_is_false_done() -> None:
    evidence = _canonical_evidence(actions=["click"])
    evidence["external_oracle_passed"] = False

    result = campaign.reconcile_action_evidence(**evidence)

    assert result["passed"] is False
    assert result["summary"]["false_done_count"] == 1
    assert {"code": "false_done", "action_id": ""} in result["errors"]
    assert result["safety_abort_reasons"] == []
    campaign._validate_action_reconciliation(result)


def test_action_reconciliation_cannot_self_declare_a_passing_verdict() -> None:
    evidence = _canonical_evidence(actions=["click"])
    evidence["browser_events"] = []
    failed = campaign.reconcile_action_evidence(**evidence)
    assert failed["passed"] is False
    forged = copy.deepcopy(failed)
    forged.pop("content_sha256")
    forged["passed"] = True
    forged = campaign.with_content_hash(forged)

    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="verdict is self-inconsistent",
    ):
        campaign._validate_action_reconciliation(forged)


@pytest.mark.parametrize(
    ("actions", "changed_index"),
    [
        (["click", "double_click"], 1),
        (["scroll", "scroll"], 2),
        (["type_text", "hotkey"], 2),
    ],
)
def test_all_action_families_fail_closed_on_action_effect_disagreement(
    actions: list[str],
    changed_index: int,
) -> None:
    result = campaign.reconcile_action_evidence(
        **_canonical_evidence(
            actions=actions,
            effect_changes={changed_index: "unchanged"},
        )
    )

    assert result["passed"] is False
    assert result["summary"]["action_effect_disagreement_count"] == 1
    assert "action_effect_disagreement" in {
        error["code"] for error in result["errors"]
    }
    assert result["safety_abort_reasons"] == []


def test_unknown_action_is_counted_once_without_replay() -> None:
    result = campaign.reconcile_action_evidence(
        **_canonical_evidence(
            actions=["click"],
            outcomes={1: "outcome_unknown"},
        )
    )

    assert result["passed"] is False
    assert result["summary"]["unknown_observed_count"] == 1
    assert result["summary"]["unknown_replay_count"] == 0
    assert result["actions"][0]["physical_attempt_count"] == 1
    assert result["actions"][0]["replay_detected"] is False
    assert {"runtime_evidence_incomplete", "action_effect_disagreement"} <= {
        item["code"] for item in result["errors"]
    }


def test_unknown_replay_is_grouped_across_distinct_decision_ids() -> None:
    result = campaign.reconcile_action_evidence(
        **_canonical_evidence(
            actions=["click", "click"],
            outcomes={1: "outcome_unknown"},
        )
    )

    assert result["passed"] is False
    assert result["summary"]["unknown_observed_count"] == 1
    assert result["summary"]["unknown_replay_count"] == 1
    assert "unknown_mutation_replay" in result["safety_abort_reasons"]


def test_unknown_same_decision_second_physical_group_is_safety_abort() -> None:
    evidence = _canonical_evidence(
        actions=["click"],
        outcomes={1: "outcome_unknown"},
    )
    browser_duplicate = copy.deepcopy(evidence["browser_events"])
    os_duplicate = copy.deepcopy(evidence["os_events"])
    for offset, event in enumerate(
        [*browser_duplicate, *os_duplicate],
        start=1,
    ):
        event["sequence"] = 10_000 + offset
    evidence["browser_events"].extend(browser_duplicate)
    evidence["os_events"].extend(os_duplicate)

    result = campaign.reconcile_action_evidence(**evidence)

    assert result["passed"] is False
    assert result["summary"]["unknown_observed_count"] == 1
    assert result["summary"]["unknown_replay_count"] == 1
    assert result["actions"][0]["trusted_input_group_count"] == 2
    assert result["actions"][0]["replay_detected"] is True
    assert "unknown_mutation_replay" in result["safety_abort_reasons"]


def test_unknown_type_text_duplicated_os_key_stream_is_safety_abort() -> None:
    evidence = _canonical_evidence(
        actions=["type_text"],
        outcomes={1: "outcome_unknown"},
    )
    input_index = next(
        index
        for index, event in enumerate(evidence["browser_events"])
        if event.get("type") == "input"
    )
    first_input = evidence["browser_events"][input_index]
    expected_text = str(first_input["data"])
    midpoint = len(expected_text) // 2
    second_input = copy.deepcopy(first_input)
    first_input["data"] = expected_text[:midpoint]
    second_input["data"] = expected_text[midpoint:]
    second_input["sequence"] = int(first_input["sequence"]) + 1
    evidence["browser_events"].insert(input_index + 1, second_input)
    os_duplicate = copy.deepcopy(evidence["os_events"])
    for offset, event in enumerate(os_duplicate, start=1):
        event["sequence"] = 10_000 + offset
    evidence["os_events"].extend(os_duplicate)

    result = campaign.reconcile_action_evidence(**evidence)

    browser_inputs = [
        event
        for event in evidence["browser_events"]
        if event.get("type") == "input"
    ]
    assert len(browser_inputs) == 2
    assert "".join(str(event["data"]) for event in browser_inputs) == expected_text
    assert result["passed"] is False
    assert result["summary"]["unknown_observed_count"] == 1
    assert result["summary"]["unknown_replay_count"] == 1
    assert result["actions"][0]["trusted_input_group_count"] == 2
    assert result["actions"][0]["replay_detected"] is True
    assert "unknown_mutation_replay" in result["actions"][0]["errors"]
    assert "unknown_mutation_replay" in result["safety_abort_reasons"]


def _family_actions(sample: dict[str, Any]) -> list[str]:
    return {
        "click_double": ["click", "double_click"],
        "scroll_roundtrip": ["scroll", "scroll"],
        "type_hotkey": ["click", "hotkey", "type_text", "hotkey"],
    }[sample["family"]]


def _write_action_reconciliation(
    root: Path,
    sample: dict[str, Any],
    *,
    external_oracle_passed: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_id = f"run-{sample['execution_order']:03d}"
    obligation_id = f"obligation-{sample['execution_order']:03d}"
    evidence = _canonical_evidence(
        actions=_family_actions(sample),
        run_id=run_id,
        obligation_id=obligation_id,
        target_identity=sample["target"],
    )
    evidence["external_oracle_passed"] = external_oracle_passed
    reconciliation = campaign.reconcile_action_evidence(**evidence)
    assert reconciliation["passed"] is external_oracle_passed
    identity = campaign.write_immutable_artifact(
        root,
        (
            f"artifacts/{sample['execution_order']:03d}/"
            "action-reconciliation.json"
        ),
        reconciliation,
        kind="action_reconciliation",
    )
    return identity, reconciliation


def _safe_external_oracle(
    sample: dict[str, Any],
    *,
    passed: bool,
    attempt: int = 1,
) -> dict[str, Any]:
    actions = (
        {
            "click_double": ["click", "double_click"],
            "scroll_roundtrip": ["scroll", "scroll"],
            "type_hotkey": ["click", "hotkey", "type_text", "hotkey"],
        }[sample["family"]]
        if passed
        else []
    )
    observations = [
        {
            "external_action_index": index,
            "action": action,
            "start_event_seq": index,
            "end_event_seq": index,
            "event_types": ["synthetic_trusted_input"],
            "before_state_sha256": "e" * 64,
            "after_state_sha256": "f" * 64,
            "decidable": True,
            "actual_changed": True,
            "error_identity": {"count": 0, "sha256": "0" * 64},
        }
        for index, action in enumerate(actions, start=1)
    ]
    return {
        "format": "vnext-live-canary-external-oracle-safe/1",
        "source": "independent_vnext_canary_browser_oracle",
        "passed": passed,
        "case_id": (
            f"{sample['execution_order']:03d}-{sample['case']}-"
            f"{sample['runtime']}-attempt-{attempt}"
        ),
        "scenario": sample["scenario"],
        "seed": sample["seed"],
        "page_id_sha256": "b" * 64,
        "event_count": len(actions),
        "event_stream_sha256": "c" * 64,
        "trusted_input_count": len(actions),
        "event_type_counts": (
            {"synthetic_trusted_input": len(actions)}
            if actions
            else {}
        ),
        "wrong_input_count": 0,
        "success_event_count": int(passed),
        "action_observations": observations,
        "error_identity": {
            "count": 0 if passed else 1,
            "sha256": "d" * 64,
        },
    }


def _safe_os_input_evidence(
    sample: dict[str, Any],
    reconciliation: dict[str, Any] | None,
) -> dict[str, Any]:
    event_sequences: list[int] = []
    event_types: list[str] = []
    action_counts: dict[str, int] = {}
    decision_ids: list[str] = []

    def types_for(action: str, count: int) -> list[str]:
        if action in {"click", "double_click"}:
            assert count % 2 == 0
            return ["pointerdown", "pointerup"] * (count // 2)
        if action == "scroll":
            return ["wheel"] * count
        assert action in {"type_text", "hotkey"}
        assert count % 2 == 0
        return [
            *(["keydown"] * (count // 2)),
            *(["keyup"] * (count // 2)),
        ]

    if reconciliation is not None:
        for action in reconciliation["actions"]:
            name = action["action"]
            count = action["trusted_os_event_count"]
            sequences = action["os_event_sequences"]
            assert len(sequences) == count
            event_sequences.extend(sequences)
            event_types.extend(types_for(name, count))
            action_counts[name] = action_counts.get(name, 0) + count
            decision_ids.append(action["decision_id"])
    else:
        for name in _family_actions(sample):
            count = {
                "click": 2,
                "double_click": 4,
                "scroll": 1,
                "type_text": 2,
                "hotkey": 4,
            }[name]
            event_types.extend(types_for(name, count))
        event_sequences = list(range(1, len(event_types) + 1))

    event_type_counts = {
        name: event_types.count(name) for name in sorted(set(event_types))
    }
    raw_projection = [
        {"sequence": sequence, "type": event_type}
        for sequence, event_type in zip(
            event_sequences,
            event_types,
            strict=True,
        )
    ]
    trusted_projection = [
        {
            **event,
            "decision_ids": sorted(set(decision_ids)),
            "action_counts": {
                name: action_counts[name] for name in sorted(action_counts)
            },
        }
        for event in raw_projection
    ]
    return {
        "format": "vnext-live-canary-os-input-safe/2",
        "hook_sequence": max(event_sequences, default=0),
        "raw_event_count": len(raw_projection),
        "raw_event_stream_sha256": hashlib.sha256(
            campaign.canonical_json_bytes(raw_projection)
        ).hexdigest(),
        "trusted_action_event_count": len(event_sequences),
        "trusted_action_event_stream_sha256": hashlib.sha256(
            campaign.canonical_json_bytes(trusted_projection)
        ).hexdigest(),
        "event_sequences": event_sequences,
        "event_type_counts": event_type_counts,
        "action_counts": {
            name: action_counts[name] for name in sorted(action_counts)
        },
        "decision_ids": sorted(set(decision_ids)),
        "external_untrusted_mutation_count": 0,
        "external_untrusted_target_mutation_count": 0,
        "unexpected_injected_target_mutation_count": 0,
        "dropped_event_count": 0,
        "thread_error_present": False,
        "input_release_audit": {
            "passed": True,
            "unbalanced_key_count": 0,
            "unbalanced_button_count": 0,
            "error_identity": {"count": 0, "sha256": "3" * 64},
        },
    }


def test_safe_os_input_schema_is_selected_by_manifest_oracle_version():
    current = _safe_os_input_evidence(
        {"family": "click_double"},
        None,
    )
    legacy = copy.deepcopy(current)
    legacy["format"] = "vnext-live-canary-os-input-safe/1"
    legacy.pop("external_untrusted_target_mutation_count")

    assert campaign._validate_safe_os_input_evidence(
        legacy,
        oracle_version="independent-vnext-canary-browser-os-join/1",
    ) == legacy
    assert campaign._validate_safe_os_input_evidence(
        current,
        oracle_version="independent-vnext-canary-browser-os-join/2",
    ) == current

    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="fields are not canonical",
    ):
        campaign._validate_safe_os_input_evidence(
            legacy,
            oracle_version="independent-vnext-canary-browser-os-join/2",
        )
    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="fields are not canonical",
    ):
        campaign._validate_safe_os_input_evidence(
            current,
            oracle_version="independent-vnext-canary-browser-os-join/1",
        )


def _write_safe_attempt_result(
    root: Path,
    sample: dict[str, Any],
    *,
    model_config: dict[str, Any],
    status: str = "passed",
    observed_status: str | None = None,
    external_oracle_passed: bool = True,
    execution_valid: bool = True,
    reconciliation: dict[str, Any] | None = None,
    os_input_evidence: dict[str, Any] | None = None,
    safety_abort_reasons: list[str] | None = None,
) -> list[dict[str, Any]]:
    observed = observed_status or status
    safety_reasons = sorted(set(safety_abort_reasons or []))
    external_oracle = _safe_external_oracle(
        sample,
        passed=external_oracle_passed,
    )
    identities = [
        campaign.write_immutable_artifact(
            root,
            (
                f"artifacts/{sample['execution_order']:03d}/"
                "external-oracle.json"
            ),
            external_oracle,
            kind="external_oracle",
        )
    ]
    os_input = copy.deepcopy(
        os_input_evidence
        if os_input_evidence is not None
        else _safe_os_input_evidence(sample, reconciliation)
    )
    identities.append(
        campaign.write_immutable_artifact(
            root,
            (
                f"artifacts/{sample['execution_order']:03d}/"
                "os-input-evidence.json"
            ),
            os_input,
            kind="os_input_evidence",
        )
    )
    runtime_anchor = None
    if sample["runtime"] == "vnext" and reconciliation is not None:
        runtime_anchor = {
            "type": "runtime_evidence_sealed",
            "ts": 1_721_000_000.25,
            **reconciliation["runtime_evidence"],
        }
        identities.append(
            campaign.write_immutable_artifact(
                root,
                (
                    f"artifacts/{sample['execution_order']:03d}/"
                    "runtime-evidence-anchor.json"
                ),
                runtime_anchor,
                kind="runtime_evidence_anchor",
            )
        )
    attempt = {
        "format": "vnext-live-canary-sample-attempt-safe/1",
        "sample_key": sample["sample_key"],
        "execution_order": sample["execution_order"],
        "attempt": 1,
        "case": sample["case"],
        "family": sample["family"],
        "scenario": sample["scenario"],
        "seed": sample["seed"],
        "runtime_version": sample["runtime"],
        "runtime_budget": sample["runtime_budget"],
        "frozen_config_sha256": model_config["config_sha256"],
        "started_at": TIMESTAMP,
        "finished_at": "2026-07-27T08:00:01Z",
        "failure_phase": "complete",
        "observed_status": observed,
        "status": status,
        "passed": status == "passed",
        "invalid_reason": "",
        "first_trusted_mutation_observed": True,
        "safety_abort_reasons": safety_reasons,
        "client_projection": {
            "run_id": f"run-{sample['execution_order']:03d}",
            "outcome": "terminal" if execution_valid else "error",
            "status": "done" if execution_valid else "failed",
            "runtime_version": sample["runtime"],
            "no_legacy_fallback": sample["runtime"] == "vnext",
            "execution_valid": execution_valid,
            "passed": execution_valid and external_oracle_passed,
        },
        "config_audit": {
            "passed": True,
            "config_sha256": model_config["config_sha256"],
            "model": model_config["model"],
            "provider": model_config["provider"],
            "adapter": model_config["adapter"],
            "api_max_retries": model_config["api_max_retries"],
        },
        "target_source_audit": {},
        "target_identity_audit": {},
        "trace_audit": {
            "safety_abort_reasons": safety_reasons,
        },
        "external_settlement": {},
        "external_oracle": external_oracle,
        "os_input_evidence": os_input,
        "action_reconciliation": reconciliation,
        "runtime_evidence_anchor": runtime_anchor,
        "cleanup": {},
        "runner_error_identity": {},
        "oracle_collection_error_identity": {},
        "runtime_evidence_error_identity": {},
    }
    identities.append(
        campaign.write_immutable_artifact(
            root,
            (
                f"artifacts/{sample['execution_order']:03d}/"
                "attempt-result.json"
            ),
            attempt,
            kind="attempt_result",
        )
    )
    return identities


def _complete_passing_campaign(
    root: Path,
    journal: Any,
    manifest: dict[str, Any],
    *,
    finish: bool = True,
) -> None:
    for sample in manifest["samples"]:
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_started",
            data={"attempt": 1},
        )
        artifacts = []
        reconciliation = None
        if sample["runtime"] == "vnext":
            identity, reconciliation = _write_action_reconciliation(
                root,
                sample,
            )
            artifacts.append(identity)
        artifacts.extend(
            _write_safe_attempt_result(
                root,
                sample,
                model_config=manifest["model_config"],
                reconciliation=reconciliation,
            )
        )
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_passed",
            data={
                "attempt": 1,
                "artifacts": artifacts,
                "observed_status": "passed",
            },
        )
    if finish:
        journal.append(
            sample_key=campaign.CAMPAIGN_SAMPLE_KEY,
            kind="campaign_finished",
            data={},
        )


def test_report_counts_legacy_runtime_completion_with_failed_oracle_as_false_done(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    false_done_sample = next(
        sample for sample in manifest["samples"]
        if sample["runtime"] == "legacy"
    )
    for sample in manifest["samples"]:
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_started",
            data={"attempt": 1},
        )
        is_false_done = sample["sample_key"] == false_done_sample["sample_key"]
        artifacts = []
        reconciliation = None
        if sample["runtime"] == "vnext":
            identity, reconciliation = _write_action_reconciliation(
                root,
                sample,
            )
            artifacts.append(identity)
        artifacts.extend(
            _write_safe_attempt_result(
                root,
                sample,
                model_config=manifest["model_config"],
                status="failed" if is_false_done else "passed",
                external_oracle_passed=not is_false_done,
                execution_valid=True,
                reconciliation=reconciliation,
            )
        )
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_failed" if is_false_done else "sample_passed",
            data={
                "attempt": 1,
                "artifacts": artifacts,
                "observed_status": (
                    "failed" if is_false_done else "passed"
                ),
            },
        )
    journal.append(
        sample_key=campaign.CAMPAIGN_SAMPLE_KEY,
        kind="campaign_finished",
        data={},
    )

    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    affected = next(
        sample
        for sample in report["samples"]
        if sample["sample_key"] == false_done_sample["sample_key"]
    )

    assert report["passed"] is False
    assert report["summary"]["false_done_count"] == 1
    assert affected["attempt_verdicts"] == [
        {
            "attempt": 1,
            "runtime_completion_claimed": True,
            "external_oracle_passed": False,
            "false_done": True,
        }
    ]


def test_report_counts_vnext_verified_mission_with_failed_oracle_as_false_done(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    false_done_sample = next(
        sample for sample in manifest["samples"]
        if sample["runtime"] == "vnext"
    )
    for sample in manifest["samples"]:
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_started",
            data={"attempt": 1},
        )
        is_false_done = sample["sample_key"] == false_done_sample["sample_key"]
        artifacts = []
        reconciliation = None
        if sample["runtime"] == "vnext":
            identity, reconciliation = _write_action_reconciliation(
                root,
                sample,
                external_oracle_passed=not is_false_done,
            )
            artifacts.append(identity)
        artifacts.extend(
            _write_safe_attempt_result(
                root,
                sample,
                model_config=manifest["model_config"],
                status="failed" if is_false_done else "passed",
                external_oracle_passed=not is_false_done,
                execution_valid=True,
                reconciliation=reconciliation,
            )
        )
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_failed" if is_false_done else "sample_passed",
            data={
                "attempt": 1,
                "artifacts": artifacts,
                "observed_status": (
                    "failed" if is_false_done else "passed"
                ),
            },
        )
    journal.append(
        sample_key=campaign.CAMPAIGN_SAMPLE_KEY,
        kind="campaign_finished",
        data={},
    )

    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    affected = next(
        sample
        for sample in report["samples"]
        if sample["sample_key"] == false_done_sample["sample_key"]
    )

    assert report["passed"] is False
    assert report["summary"]["false_done_count"] == 1
    assert affected["attempt_verdicts"][0]["false_done"] is True
    assert "action_reconciliation_failed" in affected["errors"]
    assert not any(
        "mismatch" in error for error in affected["errors"]
    )


def test_report_requires_independent_oracle_and_vnext_anchor_artifacts(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    first, second = manifest["samples"][:2]
    assert first["runtime"] == "legacy"
    assert second["runtime"] == "vnext"
    for sample, omitted_kind in (
        (first, "external_oracle"),
        (second, "runtime_evidence_anchor"),
    ):
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_started",
            data={"attempt": 1},
        )
        artifacts = []
        reconciliation = None
        if sample["runtime"] == "vnext":
            identity, reconciliation = _write_action_reconciliation(
                root,
                sample,
            )
            artifacts.append(identity)
        artifacts.extend(
            identity
            for identity in _write_safe_attempt_result(
                root,
                sample,
                model_config=manifest["model_config"],
                reconciliation=reconciliation,
            )
            if identity.get("kind") != omitted_kind
        )
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_passed",
            data={
                "attempt": 1,
                "artifacts": artifacts,
                "observed_status": "passed",
            },
        )

    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    by_key = {
        sample["sample_key"]: sample for sample in report["samples"]
    }

    assert "missing_or_duplicate_external_oracle" in by_key[
        first["sample_key"]
    ]["errors"]
    assert "missing_or_duplicate_runtime_evidence_anchor" in by_key[
        second["sample_key"]
    ]["errors"]


@pytest.mark.parametrize(
    ("mode", "expected_error"),
    [
        ("missing", "missing_or_duplicate_os_input_evidence"),
        ("duplicate", "missing_or_duplicate_os_input_evidence"),
        ("replacement", "attempt_os_input_evidence_mismatch"),
        ("deleted", "missing_or_duplicate_os_input_evidence"),
        ("tampered", "missing_or_duplicate_os_input_evidence"),
    ],
)
def test_report_fails_closed_for_noncanonical_independent_os_input_artifact(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
    mode: str,
    expected_error: str,
) -> None:
    root, journal = sealed_campaign
    sample = next(
        item for item in manifest["samples"] if item["runtime"] == "legacy"
    )
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    artifacts = _write_safe_attempt_result(
        root,
        sample,
        model_config=manifest["model_config"],
    )
    os_identity = next(
        item for item in artifacts if item["kind"] == "os_input_evidence"
    )
    os_path = root / Path(*os_identity["path"].split("/"))
    os_value = json.loads(os_path.read_text(encoding="utf-8"))
    if mode == "missing":
        artifacts = [
            item for item in artifacts
            if item["kind"] != "os_input_evidence"
        ]
    elif mode == "duplicate":
        artifacts.append(
            campaign.write_immutable_artifact(
                root,
                (
                    f"artifacts/{sample['execution_order']:03d}/"
                    "os-input-evidence-duplicate.json"
                ),
                os_value,
                kind="os_input_evidence",
            )
        )
    elif mode == "replacement":
        replacement = copy.deepcopy(os_value)
        replacement["raw_event_stream_sha256"] = "9" * 64
        replacement_identity = campaign.write_immutable_artifact(
            root,
            (
                f"artifacts/{sample['execution_order']:03d}/"
                "os-input-evidence-replacement.json"
            ),
            replacement,
            kind="os_input_evidence",
        )
        artifacts = [
            replacement_identity
            if item["kind"] == "os_input_evidence"
            else item
            for item in artifacts
        ]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_passed",
        data={
            "attempt": 1,
            "artifacts": artifacts,
            "observed_status": "passed",
        },
    )
    if mode == "deleted":
        os_path.unlink()
    elif mode == "tampered":
        os_path.write_bytes(b'{"tampered":true}\n')

    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    affected = next(
        item
        for item in report["samples"]
        if item["sample_key"] == sample["sample_key"]
    )

    assert report["passed"] is False
    assert affected["report_status"] == "failed"
    assert expected_error in affected["errors"]
    if mode in {"deleted", "tampered"}:
        assert any(
            error.startswith("artifact_integrity:")
            for error in affected["errors"]
        )


def test_report_rejects_passing_legacy_attempt_with_empty_os_evidence(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    sample = next(
        item for item in manifest["samples"] if item["runtime"] == "legacy"
    )
    empty_stream_sha256 = hashlib.sha256(
        campaign.canonical_json_bytes([])
    ).hexdigest()
    empty_os = _safe_os_input_evidence(sample, None)
    empty_os.update(
        {
            "hook_sequence": 0,
            "raw_event_count": 0,
            "raw_event_stream_sha256": empty_stream_sha256,
            "trusted_action_event_count": 0,
            "trusted_action_event_stream_sha256": (
                empty_stream_sha256
            ),
            "event_sequences": [],
            "event_type_counts": {},
            "action_counts": {},
            "decision_ids": [],
        }
    )
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    artifacts = _write_safe_attempt_result(
        root,
        sample,
        model_config=manifest["model_config"],
        os_input_evidence=empty_os,
    )
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_passed",
        data={
            "attempt": 1,
            "artifacts": artifacts,
            "observed_status": "passed",
        },
    )

    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    affected = next(
        item
        for item in report["samples"]
        if item["sample_key"] == sample["sample_key"]
    )

    assert report["passed"] is False
    assert affected["report_status"] == "failed"
    assert any(
        "passing legacy OS input evidence is not independently classified"
        in error
        for error in affected["errors"]
    )


def _complete_manifest_prefix_with_grounded_failures(
    root: Path,
    journal: Any,
    manifest: dict[str, Any],
    *,
    target_sample_key: str,
) -> list[str]:
    completed: list[str] = []
    target_found = False
    for sample in manifest["samples"]:
        if sample["sample_key"] == target_sample_key:
            target_found = True
            break
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_started",
            data={"attempt": 1},
        )
        artifacts = _write_safe_attempt_result(
            root,
            sample,
            model_config=manifest["model_config"],
            status="failed",
            external_oracle_passed=True,
            execution_valid=False,
        )
        failed_data = _failed_data()
        failed_data.update(
            {
                "artifacts": artifacts,
                "observed_status": "failed",
            }
        )
        journal.append(
            sample_key=sample["sample_key"],
            kind="sample_failed",
            data=failed_data,
        )
        completed.append(sample["sample_key"])
    assert target_found
    return completed


@pytest.mark.parametrize(
    "violation",
    ["direct-second", "restart-terminal", "skip-running"],
)
def test_manifest_order_rejects_public_transition_violations(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
    violation: str,
) -> None:
    _, journal = sealed_campaign
    first, second = manifest["samples"][:2]
    if violation == "restart-terminal":
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
    elif violation == "skip-running":
        journal.append(
            sample_key=first["sample_key"],
            kind="sample_started",
            data={"attempt": 1},
        )
    before = journal.path.read_bytes()

    with pytest.raises(campaign.CampaignTransitionError):
        if violation == "direct-second":
            journal.append(
                sample_key=second["sample_key"],
                kind="sample_started",
                data={"attempt": 1},
            )
        elif violation == "restart-terminal":
            journal.append(
                sample_key=first["sample_key"],
                kind="sample_started",
                data={"attempt": 1},
            )
        else:
            journal.append(
                sample_key=second["sample_key"],
                kind="sample_started",
                data={"attempt": 1},
            )

    assert journal.path.read_bytes() == before


def test_ordinary_grounded_failure_allows_next_manifest_sample_to_start(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    first, second = manifest["samples"][:2]
    completed = _complete_manifest_prefix_with_grounded_failures(
        root,
        journal,
        manifest,
        target_sample_key=second["sample_key"],
    )

    assert completed == [first["sample_key"]]
    first_state = journal.state()["samples"][first["sample_key"]]
    assert first_state["status"] == "failed"
    assert {
        artifact["kind"]
        for artifact in first_state["attempts"][0]["artifacts"]
    } == {"attempt_result", "external_oracle", "os_input_evidence"}
    journal.append(
        sample_key=second["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    assert journal.state()["samples"][second["sample_key"]]["status"] == (
        "running"
    )


def test_report_cross_binds_os_input_to_vnext_reconciliation_facts(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    sample = next(
        item for item in manifest["samples"] if item["runtime"] == "vnext"
    )
    completed = _complete_manifest_prefix_with_grounded_failures(
        root,
        journal,
        manifest,
        target_sample_key=sample["sample_key"],
    )
    assert completed
    assert all(
        journal.state()["samples"][sample_key]["status"] == "failed"
        for sample_key in completed
    )
    assert journal.state()["samples"][sample["sample_key"]]["status"] == (
        "planned"
    )
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    assert journal.state()["samples"][sample["sample_key"]]["status"] == (
        "running"
    )
    reconciliation_identity, reconciliation = _write_action_reconciliation(
        root,
        sample,
    )
    os_input = _safe_os_input_evidence(sample, reconciliation)
    os_input["decision_ids"] = ["decision-replaced"]
    artifacts = [
        reconciliation_identity,
        *_write_safe_attempt_result(
            root,
            sample,
            model_config=manifest["model_config"],
            reconciliation=reconciliation,
            os_input_evidence=os_input,
        ),
    ]
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_passed",
        data={
            "attempt": 1,
            "artifacts": artifacts,
            "observed_status": "passed",
        },
    )

    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    affected = next(
        item
        for item in report["samples"]
        if item["sample_key"] == sample["sample_key"]
    )

    assert report["passed"] is False
    assert affected["report_status"] == "failed"
    assert "os_input_reconciliation_binding_mismatch" in affected["errors"]
    assert "attempt_os_input_evidence_mismatch" not in affected["errors"]


def test_trace_fallback_safety_reason_is_counted_and_timeout_is_normalized(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    sample = manifest["samples"][0]
    assert sample["runtime"] == "legacy"
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_started",
        data={"attempt": 1},
    )
    artifacts = _write_safe_attempt_result(
        root,
        sample,
        model_config=manifest["model_config"],
        status="failed",
        observed_status="timeout",
        external_oracle_passed=True,
        execution_valid=True,
        safety_abort_reasons=["runtime_legacy_fallback"],
    )
    journal.append(
        sample_key=sample["sample_key"],
        kind="sample_failed",
        data={
            "attempt": 1,
            "artifacts": artifacts,
            "observed_status": "timeout",
        },
    )
    journal.append(
        sample_key=sample["sample_key"],
        kind="safety_abort",
        data={"reason": "runtime_legacy_fallback"},
    )
    journal.append(
        sample_key=campaign.CAMPAIGN_SAMPLE_KEY,
        kind="campaign_finished",
        data={},
    )

    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    affected = report["samples"][0]

    assert report["summary"]["legacy_fallback_count"] == 1
    assert affected["report_status"] == "failed"
    assert not any(
        error.startswith("artifact_integrity:")
        for error in affected["errors"]
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


def test_report_cannot_pass_without_campaign_finished(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    _complete_passing_campaign(root, journal, manifest, finish=False)

    report = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )

    assert report["passed"] is False
    assert report["campaign_errors"] == ["campaign_finished_missing"]
    assert report["campaign_state"] == "running"


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
    with pytest.raises(campaign.CampaignIntegrityError):
        campaign.seal_campaign_report(root, changed)


def test_forged_first_write_report_is_rejected_by_independent_derivation(
    sealed_campaign: tuple[Path, Any],
    manifest: dict[str, Any],
) -> None:
    root, journal = sealed_campaign
    _complete_passing_campaign(root, journal, manifest)
    derived = campaign.derive_campaign_report(
        manifest,
        journal.load(),
        campaign_directory=root,
    )
    forged = copy.deepcopy(derived)
    forged.pop("content_sha256")
    forged["passed"] = False
    forged = campaign.with_content_hash(forged)

    with pytest.raises(
        campaign.CampaignIntegrityError,
        match="differs from sealed manifest",
    ):
        campaign.seal_campaign_report(root, forged)

    assert not (root / campaign.REPORT_FILENAME).exists()


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
