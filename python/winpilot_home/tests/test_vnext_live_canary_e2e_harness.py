from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
import sys
from types import SimpleNamespace
import uuid
from pathlib import Path
from typing import Any

import pytest

from winpilot.vnext.actor import ActorDecisionEnvelope
from winpilot.vnext.evidence_plane import (
    ActorDecisionRecord,
    PhysicalAttemptRecord,
    RuntimeEvidenceReport,
    canonical_sha256,
)
from winpilot.vnext.model_roles import (
    VISUAL_ACTOR_MODEL_ROLE,
    VISUAL_VERIFIER_MODEL_ROLE,
)


WORKSPACE = Path(__file__).resolve().parents[3]
_MISSING = object()


def _assert_exact_json_value(
    observed: Any,
    expected: Any,
    *,
    path: str = "$",
) -> None:
    assert type(observed) is type(expected), (
        f"{path} changed JSON type from "
        f"{type(expected).__name__} to {type(observed).__name__}"
    )
    if isinstance(expected, dict):
        assert list(observed) == list(expected), f"{path} changed object fields"
        for key, value in expected.items():
            _assert_exact_json_value(
                observed[key],
                value,
                path=f"{path}.{key}",
            )
        return
    if isinstance(expected, list):
        assert len(observed) == len(expected), f"{path} changed list length"
        for index, (actual_item, expected_item) in enumerate(
            zip(observed, expected, strict=True)
        ):
            _assert_exact_json_value(
                actual_item,
                expected_item,
                path=f"{path}[{index}]",
            )
        return
    assert observed == expected, f"{path} changed value"


def _complete_campaign_config(
    harness,
    *,
    temperature: Any = 0.0,
) -> dict[str, Any]:
    api = {
        "provider": "openai",
        "base_url": "https://example.invalid/v1",
        "api_key": "private-test-key",
        "model": "model-one",
        "adapter": "OpenAICompatibleAdapter",
        "temperature": temperature,
        "max_tokens": 1024,
        "request_timeout_s": 30,
        "request_total_timeout_s": 60,
        "max_retries": 2,
        "done_judge_model": "",
        "thinking": False,
    }
    if temperature is _MISSING:
        api.pop("temperature")
    return {
        "api": api,
        "vlm": {
            "provider": "openai",
            "base_url": "https://example.invalid/v1",
            "api_key": "private-vlm-test-key",
            "model": "model-one",
            "thinking": False,
        },
        "agent": {
            "escalation_model": "",
            "llm_trace": True,
        },
        "vnext": {
            "live_canary_enabled": False,
            **harness.FROZEN_RUNTIME_BUDGET,
        },
    }


def _write_config_fixture(
    path: Path,
    config: dict[str, Any],
) -> bytes:
    data = (
        json.dumps(
            config,
            ensure_ascii=False,
            indent=2,
            allow_nan=True,
        )
        + "\n"
    ).encode("utf-8")
    path.write_bytes(data)
    return data


def _config_contract_sha256(harness, config: dict[str, Any]) -> str:
    contract = harness.campaign_state.redacted_config_contract(config)
    assert contract["config_sha256"] == hashlib.sha256(
        harness.campaign_state.canonical_json_bytes(
            contract["redacted_config"]
        )
    ).hexdigest()
    return str(contract["config_sha256"])


def _load(path: Path, name: str):
    unique_name = f"{name}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(unique_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _client_harness():
    return _load(
        WORKSPACE / "scripts" / "client_e2e_run.py",
        "client_e2e_run_canary_test",
    )


def _canary_harness():
    return _load(
        WORKSPACE / "scripts" / "client_vnext_canary_e2e_run.py",
        "client_vnext_canary_e2e_run_test",
    )


def _oracle():
    return _load(
        WORKSPACE
        / "python"
        / "winpilot_home"
        / "eval_targets"
        / "vnext_canary_server.py",
        "vnext_canary_server_test",
    )


def _input_hook():
    return _load(
        WORKSPACE / "scripts" / "vnext_canary_input_hook.py",
        "vnext_canary_input_hook_test",
    )


def _stack(harness) -> dict[str, Any]:
    return {
        "master_commit": harness.MASTER_COMMIT,
        "c1": {
            "pr": 27,
            "base": "master",
            "head": harness.C1_HEAD,
        },
        "c2": {
            "pr": 28,
            "base": harness.C1_BRANCH,
            "head": harness.C2_HEAD,
        },
        "c3": {
            "pr": 29,
            "base": harness.C2_BRANCH,
            "head": harness.C3_HEAD,
        },
        "evidence_plane": {
            "pr": 32,
            "base": harness.C3_BRANCH,
            "head": harness.EVIDENCE_PLANE_HEAD,
        },
        "evidence_persistence": {
            "pr": 34,
            "base": harness.EVIDENCE_PLANE_BRANCH,
            "head": harness.EVIDENCE_PERSISTENCE_HEAD,
        },
        "c4": {
            "base": harness.EVIDENCE_PERSISTENCE_BRANCH,
            "commit": "f" * 40,
            "pr_head": "f" * 40,
            "merge_ref": "e" * 40,
        },
    }


def _model_config(harness) -> dict[str, Any]:
    return harness.campaign_state.redacted_config_contract(
        {
            "api": {
                "provider": "openai",
                "model": "model-one",
                "adapter": "OpenAICompatibleAdapter",
                "temperature": 0.0,
                "max_tokens": 1024,
                "max_retries": 0,
            },
            "vlm": {
                "provider": "openai",
                "model": "model-one",
            },
            "agent": {
                "llm_trace": True,
                "escalation_model": "",
            },
            "vnext": {
                "live_canary_enabled": True,
                **harness.FROZEN_RUNTIME_BUDGET,
            },
        }
    )


def _manifest(
    harness,
    *,
    campaign_id: str = "vnext-c4-test-paired",
) -> dict[str, Any]:
    digest = "a" * 64
    return harness.campaign_state.build_manifest(
        campaign_id=campaign_id,
        created_at="2026-07-27T12:00:00Z",
        stack=_stack(harness),
        source_identity={
            "harness_code_commit": "f" * 40,
            "files": {
                "scripts/client_vnext_canary_e2e_run.py": digest,
            },
        },
        build_identity={
            "electron": {"sha256": digest},
            "package_lock": {"sha256": digest},
            "main": {"sha256": digest},
            "renderer": {"sha256": digest},
            "runtime": {
                "expected_loaded_commit": "f" * 40,
                "expected_current_commit": "f" * 40,
            },
        },
        model_config=_model_config(harness),
        matrix=harness.CANARY_MATRIX,
        task=harness.CANARY_TASK,
        target={"mode": "fresh_prevalidated_prebound_target"},
        runtime_budget=harness.FROZEN_RUNTIME_BUDGET,
        oracle_version=harness.ORACLE_VERSION,
        c2_unknown_no_replay={
            "c2_head": harness.C2_HEAD,
            "actions_run_id": harness.C2_ACTIONS_RUN_ID,
            "code_sha256": digest,
            "tests_sha256": digest,
            "test_nodes": sorted(harness.C2_EVIDENCE_TEST_NODES),
        },
    )


def test_visible_client_selector_is_exact_and_read_back(monkeypatch):
    harness = _client_harness()
    state = {"label": "Legacy", "clicked": 0}

    class ValuePattern:
        @property
        def Value(self):
            return state["label"]

    class Combo:
        Name = harness.RUNTIME_SELECTOR

        def Click(self):
            state["clicked"] += 1

        def GetPattern(self, pattern_id):
            if pattern_id == harness.auto.PatternId.ValuePattern:
                return ValuePattern()
            return None

    def send_keys(keys):
        if keys == "{Home}":
            state["label"] = "Legacy"
        elif keys == "{Down}":
            state["label"] = "vNext"

    monkeypatch.setattr(harness.auto, "SendKeys", send_keys)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    selected = harness._select_runtime_version(Combo(), "vnext")
    assert selected == {
        "runtime_version": "vnext",
        "runtime_selector_label": "vNext",
    }
    assert state["clicked"] == 1

    selected = harness._select_runtime_version(Combo(), "legacy")
    assert selected["runtime_version"] == "legacy"
    with pytest.raises(ValueError):
        harness._select_runtime_version(Combo(), "future")


def test_closed_matrix_has_two_fixed_seeds_per_family_and_no_sample_overrides():
    harness = _canary_harness()
    grouped: dict[str, list[int]] = {}
    for item in harness.CANARY_MATRIX:
        grouped.setdefault(item["family"], []).append(item["seed"])
        assert tuple(item["runtime_order"]) in {
            ("legacy", "vnext"),
            ("vnext", "legacy"),
        }
        assert sorted(item["runtime_order"]) == ["legacy", "vnext"]
    assert grouped == {
        "click_double": [4101, 4102],
        "scroll_roundtrip": [5201, 5202],
        "type_hotkey": [6301, 6302],
    }
    assert harness.FROZEN_RUNTIME_BUDGET == {
        "max_turns": 30,
        "max_runtime_s": 300,
        "max_model_calls": 60,
    }
    assert tuple(inspect.signature(harness.run_suite).parameters) == ()
    source = Path(harness.__file__).read_text(encoding="utf-8")
    for forbidden in (
        'add_argument("--seed"',
        'add_argument("--timeout"',
        'add_argument("--max-steps"',
        'add_argument("--case"',
        'add_argument("--suite"',
        'add_argument("--runtime"',
        'add_argument("--output-root"',
    ):
        assert forbidden not in source
    assert "WaitForSingleObject(handle, 0)" in source


def test_manifest_predeclares_every_unrun_member_before_execution():
    harness = _canary_harness()
    manifest = _manifest(harness)
    assert manifest["stack"]["evidence_plane"] == {
        "pr": 32,
        "base": harness.C3_BRANCH,
        "head": harness.EVIDENCE_PLANE_HEAD,
    }
    assert manifest["stack"]["evidence_persistence"] == {
        "pr": 34,
        "base": harness.EVIDENCE_PLANE_BRANCH,
        "head": harness.EVIDENCE_PERSISTENCE_HEAD,
    }
    assert manifest["stack"]["c4"]["base"] == (
        harness.EVIDENCE_PERSISTENCE_BRANCH
    )
    assert len(manifest["samples"]) == 12
    assert [
        sample["runtime"]
        for sample in manifest["samples"]
    ] == [
        runtime
        for item in harness.CANARY_MATRIX
        for runtime in item["runtime_order"]
    ]
    assert [item["execution_order"] for item in manifest["samples"]] == list(
        range(1, 13)
    )


def _install_fake_campaign_environment(
    harness,
    monkeypatch,
    tmp_path: Path,
    *,
    fake_run,
    lifecycle: list[str] | None = None,
) -> None:
    events = lifecycle if lifecycle is not None else []

    class Mutex:
        def __enter__(self):
            events.append("mutex")
            return self

        def __exit__(self, *_args):
            return None

    class Hook:
        def start(self):
            assert (
                tmp_path
                / "paired-campaign"
                / harness.campaign_state.MANIFEST_FILENAME
            ).is_file()
            events.append("hook_start")
            return self

        def stop(self):
            events.append("hook_stop")

        def snapshot(self):
            return {
                "events": [],
                "hook_seq": 0,
                "dropped_event_count": 0,
                "thread_error": "",
            }

    monkeypatch.setattr(harness, "DesktopSuiteMutex", Mutex)
    monkeypatch.setattr(harness, "DEFAULT_OUTPUT", tmp_path)
    monkeypatch.setattr(harness, "_workspace_electron_processes", lambda: [])
    monkeypatch.setattr(
        harness,
        "_preflight_stack",
        lambda: (_stack(harness), {"fresh_checks": []}),
    )
    monkeypatch.setattr(
        harness,
        "_fixed_config",
        lambda: (b"{}\n", {"vnext": {}}, _model_config(harness)),
    )
    monkeypatch.setattr(
        harness,
        "_restore_config_bytes",
        lambda _value: {"passed": True, "errors": []},
    )
    monkeypatch.setattr(
        harness,
        "_build_campaign_manifest",
        lambda **kwargs: _manifest(
            harness,
            campaign_id=kwargs["campaign_id"],
        ),
    )
    monkeypatch.setattr(harness.input_hook, "PassiveInputHook", Hook)
    monkeypatch.setattr(harness, "run_runtime_case", fake_run)


def _fake_attempt(
    harness,
    *,
    sample: dict[str, Any],
    attempt: int,
    campaign_dir: Path,
    status: str = "failed",
    invalid_reason: str = "",
    pre_mutation: bool = False,
    safety_abort_reasons: list[str] | None = None,
) -> dict[str, Any]:
    case_id = (
        f"{int(sample['execution_order']):03d}-"
        f"{sample['case']}-{sample['runtime']}-attempt-{attempt}"
    )
    external = harness._safe_external_oracle(
        {"events": []},
        {
            "source": "independent_vnext_canary_browser_oracle",
            "passed": False,
            "case_id": case_id,
            "scenario": sample["scenario"],
            "seed": sample["seed"],
            "page_id": f"synthetic:{case_id}",
            "errors": ["synthetic ordinary failure"],
        },
        expected_case_id=case_id,
        expected_scenario=sample["scenario"],
        expected_seed=sample["seed"],
    )
    os_evidence = harness._safe_os_input_evidence(
        {
            "raw_events": [],
            "normalized_action_events": [],
            "external_untrusted_mutations": [],
            "unexpected_injected_target_mutations": [],
            "dropped_event_count": 0,
            "hook_seq": 0,
            "thread_error": "",
            "input_release_audit": {
                "passed": True,
                "key_balance": {},
                "button_balance": {},
                "errors": [],
            },
        }
    )
    external_identity = harness.campaign_state.write_immutable_artifact(
        campaign_dir,
        harness._stable_artifact_path(
            sample,
            attempt,
            "external-oracle-safe.json",
        ),
        external,
        kind="external_oracle",
    )
    os_identity = harness.campaign_state.write_immutable_artifact(
        campaign_dir,
        harness._stable_artifact_path(
            sample,
            attempt,
            "os-input-evidence-safe.json",
        ),
        os_evidence,
        kind="os_input_evidence",
    )
    artifacts = [external_identity, os_identity]
    if status == "invalid" and pre_mutation:
        artifacts.append(
            harness.campaign_state.write_immutable_artifact(
                campaign_dir,
                harness._stable_artifact_path(
                    sample,
                    attempt,
                    "invalid-run-evidence.json",
                ),
                {
                    "format": "vnext-live-canary-invalid-run-evidence/1",
                    "reason": invalid_reason,
                    "pre_mutation": True,
                    "first_trusted_mutation_observed": False,
                    "external_oracle_sha256": external_identity["sha256"],
                    "os_input_evidence_sha256": os_identity["sha256"],
                    "trusted_action_event_count": 0,
                    "external_untrusted_mutation_count": 0,
                    "dropped_event_count": 0,
                    "thread_error_present": False,
                },
                kind="invalid_run_evidence",
            )
        )
    return {
        "status": status,
        "passed": status == "passed",
        "invalid_reason": invalid_reason,
        "pre_mutation": pre_mutation,
        "external_evidence": True,
        "first_trusted_mutation_observed": not pre_mutation,
        "safety_abort_reasons": list(safety_abort_reasons or []),
        "artifacts": artifacts,
    }


def test_suite_seals_before_hook_runs_all_twelve_after_ordinary_failures(
    monkeypatch,
    tmp_path,
):
    harness = _canary_harness()
    calls: list[tuple[str, int]] = []
    lifecycle: list[str] = []

    def fake_run(sample, attempt, *, campaign_dir, **_kwargs):
        lifecycle.append(f"run:{sample['sample_key']}:{attempt}")
        calls.append((sample["sample_key"], attempt))
        return _fake_attempt(
            harness,
            sample=sample,
            attempt=attempt,
            campaign_dir=campaign_dir,
        )

    _install_fake_campaign_environment(
        harness,
        monkeypatch,
        tmp_path,
        fake_run=fake_run,
        lifecycle=lifecycle,
    )
    report = harness.run_suite()

    assert len(calls) == 12
    assert calls == [
        (sample["sample_key"], 1)
        for sample in _manifest(harness)["samples"]
    ]
    assert lifecycle.index("hook_start") < lifecycle.index(
        f"run:{calls[0][0]}:1"
    )
    assert report["campaign_state"] == "failed"
    assert report["passed"] is False
    assert len(report["samples"]) == 12
    assert all(
        sample["report_status"] == "failed"
        for sample in report["samples"]
    )
    campaign_dir = tmp_path / "paired-campaign"
    manifest_path = campaign_dir / harness.campaign_state.MANIFEST_FILENAME
    manifest_bytes = manifest_path.read_bytes()
    events = harness.campaign_state.load_journal_events(
        campaign_dir / harness.campaign_state.JOURNAL_FILENAME,
        harness.campaign_state.load_manifest(campaign_dir),
    )
    assert events[-1]["kind"] == "campaign_finished"
    assert manifest_path.read_bytes() == manifest_bytes

    refused = harness.run_suite()
    assert refused["status"] == "invalid"
    assert refused["error_code"] == "campaign_already_claimed"
    assert len(calls) == 12


def test_only_objective_pre_mutation_invalid_gets_one_same_seed_rerun(
    monkeypatch,
    tmp_path,
):
    harness = _canary_harness()
    calls: list[tuple[str, int]] = []

    def fake_run(sample, attempt, *, campaign_dir, **_kwargs):
        calls.append((sample["sample_key"], attempt))
        if len(calls) == 1:
            return _fake_attempt(
                harness,
                sample=sample,
                attempt=attempt,
                campaign_dir=campaign_dir,
                status="invalid",
                invalid_reason="electron_not_started_pre_mutation",
                pre_mutation=True,
            )
        return _fake_attempt(
            harness,
            sample=sample,
            attempt=attempt,
            campaign_dir=campaign_dir,
        )

    _install_fake_campaign_environment(
        harness,
        monkeypatch,
        tmp_path,
        fake_run=fake_run,
    )
    report = harness.run_suite()
    first_key = _manifest(harness)["samples"][0]["sample_key"]
    assert calls[:2] == [(first_key, 1), (first_key, 2)]
    assert len(calls) == 13
    assert report["samples"][0]["attempts"][0]["status"] == "invalid"
    assert report["samples"][0]["attempts"][1]["status"] == "failed"
    events = harness.campaign_state.load_journal_events(
        tmp_path / "paired-campaign" / harness.campaign_state.JOURNAL_FILENAME,
        harness.campaign_state.load_manifest(tmp_path / "paired-campaign"),
    )
    assert sum(
        event["kind"] == "sample_rerun_authorized"
        for event in events
    ) == 1


def test_invalid_after_any_mutation_is_a_failure_not_a_rerun_candidate():
    harness = _canary_harness()
    kind, data = harness._terminal_event(
        {
            "status": "invalid",
            "passed": False,
            "invalid_reason": "",
            "pre_mutation": False,
            "external_evidence": True,
            "first_trusted_mutation_observed": True,
            "artifacts": [],
        },
        attempt=1,
    )
    assert kind == "sample_failed"
    assert data["observed_status"] == "invalid"


def test_safety_critical_failure_aborts_but_still_finishes_journal(
    monkeypatch,
    tmp_path,
):
    harness = _canary_harness()
    calls: list[str] = []

    def fake_run(sample, attempt, *, campaign_dir, **_kwargs):
        calls.append(sample["sample_key"])
        return _fake_attempt(
            harness,
            sample=sample,
            attempt=attempt,
            campaign_dir=campaign_dir,
            safety_abort_reasons=["identity_admission_bypass"],
        )

    _install_fake_campaign_environment(
        harness,
        monkeypatch,
        tmp_path,
        fake_run=fake_run,
    )
    report = harness.run_suite()
    assert len(calls) == 1
    assert report["campaign_state"] == "safety_aborted"
    assert report["abort_event"]["reason"] == "identity_admission_bypass"
    assert report["samples"][0]["report_status"] == "failed"
    assert all(
        sample["report_status"] == "not_run_due_to_safety_abort"
        for sample in report["samples"][1:]
    )
    events = harness.campaign_state.load_journal_events(
        tmp_path / "paired-campaign" / harness.campaign_state.JOURNAL_FILENAME,
        harness.campaign_state.load_manifest(tmp_path / "paired-campaign"),
    )
    assert [event["kind"] for event in events[-2:]] == [
        "safety_abort",
        "campaign_finished",
    ]


def test_campaign_temporarily_freezes_retry_and_restores_exact_config(
    monkeypatch,
    tmp_path,
):
    harness = _canary_harness()
    config_path = tmp_path / "config.json"
    original = _complete_campaign_config(harness, temperature=0.0)
    original_bytes = _write_config_fixture(config_path, original)
    original_sha256 = _config_contract_sha256(harness, original)
    monkeypatch.setattr(harness, "CONFIG_PATH", config_path)

    captured_bytes, frozen_template, frozen_contract = harness._fixed_config()

    expected_frozen = copy.deepcopy(original)
    expected_frozen["api"]["max_retries"] = 0
    expected_frozen["vnext"].update(
        {
            "live_canary_enabled": True,
            **harness.FROZEN_RUNTIME_BUDGET,
        }
    )
    assert captured_bytes == original_bytes
    _assert_exact_json_value(frozen_template, expected_frozen)
    persisted_frozen = json.loads(config_path.read_text(encoding="utf-8"))
    _assert_exact_json_value(persisted_frozen, expected_frozen)
    assert type(persisted_frozen["api"]["temperature"]) is float
    assert persisted_frozen["api"]["temperature"] == 0.0
    assert type(persisted_frozen["api"]["max_retries"]) is int
    assert persisted_frozen["api"]["max_retries"] == 0
    assert frozen_contract["temperature"] == 0.0
    assert type(frozen_contract["temperature"]) is float
    assert frozen_contract["api_max_retries"] == 0
    assert frozen_contract["config_sha256"] == _config_contract_sha256(
        harness,
        expected_frozen,
    )
    assert frozen_contract["config_sha256"] != original_sha256

    monkeypatch.setattr(
        harness,
        "_config_request",
        lambda _driver, **_kwargs: copy.deepcopy(persisted_frozen),
    )
    authenticated = harness._authenticated_config(
        object(),
        frozen_template=frozen_template,
    )
    _assert_exact_json_value(authenticated, frozen_contract)
    assert authenticated["api_max_retries"] == 0

    restored = harness._restore_config_bytes(captured_bytes)
    assert restored == {"passed": True, "errors": []}
    assert config_path.read_bytes() == original_bytes
    restored_config = json.loads(config_path.read_text(encoding="utf-8"))
    _assert_exact_json_value(restored_config, original)
    assert _config_contract_sha256(harness, restored_config) == original_sha256


def test_campaign_preparation_failure_restores_exact_config(
    monkeypatch,
    tmp_path,
):
    harness = _canary_harness()
    config_path = tmp_path / "config.json"
    original = _complete_campaign_config(harness, temperature=0.0)
    original_bytes = _write_config_fixture(config_path, original)
    original_sha256 = _config_contract_sha256(harness, original)

    class Mutex:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    expected_stack = _stack(harness)
    preparation = {"manifest_called": False}

    def fail_manifest(**kwargs):
        preparation["manifest_called"] = True
        _assert_exact_json_value(kwargs["stack"], expected_stack)
        assert kwargs["admission"] == {"passed": True}
        raise RuntimeError("synthetic preparation failure")

    monkeypatch.setattr(harness, "CONFIG_PATH", config_path)
    monkeypatch.setattr(harness, "DEFAULT_OUTPUT", tmp_path / "campaign-output")
    monkeypatch.setattr(harness, "DesktopSuiteMutex", Mutex)
    monkeypatch.setattr(harness, "_workspace_electron_processes", lambda: [])
    monkeypatch.setattr(
        harness,
        "_preflight_stack",
        lambda: (copy.deepcopy(expected_stack), {"passed": True}),
    )
    monkeypatch.setattr(harness, "_build_campaign_manifest", fail_manifest)

    result = None
    try:
        result = harness.run_suite()
    finally:
        assert config_path.read_bytes() == original_bytes
        restored_config = json.loads(config_path.read_text(encoding="utf-8"))
        _assert_exact_json_value(restored_config, original)
        assert (
            _config_contract_sha256(harness, restored_config)
            == original_sha256
        )

    assert preparation["manifest_called"] is True
    assert result is not None
    assert result["status"] == "invalid"
    assert result["error_code"] == "campaign_preflight_or_seal_failed"


@pytest.mark.parametrize(
    "temperature",
    [
        pytest.param(_MISSING, id="missing"),
        pytest.param(None, id="none"),
        pytest.param(True, id="bool"),
        pytest.param("0.0", id="numeric-string"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
    ],
)
def test_campaign_rejects_missing_or_nonfinite_json_temperature_without_default(
    monkeypatch,
    tmp_path,
    temperature,
):
    harness = _canary_harness()
    config_path = tmp_path / "config.json"
    original = _complete_campaign_config(
        harness,
        temperature=temperature,
    )
    original_bytes = _write_config_fixture(config_path, original)
    monkeypatch.setattr(harness, "CONFIG_PATH", config_path)

    with pytest.raises(
        harness.campaign_state.CampaignStateError,
        match="temperature|finite JSON number",
    ):
        harness._fixed_config()

    assert config_path.read_bytes() == original_bytes


def test_campaign_rejects_temperature_outside_finite_float_range(
    monkeypatch,
    tmp_path,
):
    harness = _canary_harness()
    config_path = tmp_path / "config.json"
    original = _complete_campaign_config(
        harness,
        temperature=10**400,
    )
    original_bytes = _write_config_fixture(config_path, original)
    monkeypatch.setattr(harness, "CONFIG_PATH", config_path)

    with pytest.raises(OverflowError):
        harness._fixed_config()

    assert config_path.read_bytes() == original_bytes


def test_same_visible_electron_identity_is_required():
    harness = _client_harness()
    expected = harness.ClientIdentity(
        pid=10,
        hwnd=20,
        executable=r"C:\Electron\electron.exe",
        command_line=["electron.exe", str(WORKSPACE)],
        workspace_verified=True,
    )
    observed = harness.ClientIdentity(
        pid=10,
        hwnd=20,
        executable=r"c:\electron\electron.exe",
        command_line=["electron.exe", str(WORKSPACE)],
        workspace_verified=True,
    )
    assert harness._client_identity_audit(observed, expected)["passed"] is True
    changed = harness.ClientIdentity(
        pid=11,
        hwnd=21,
        executable=observed.executable,
        command_line=observed.command_line,
        workspace_verified=True,
    )
    rejected = harness._client_identity_audit(changed, expected)
    assert rejected["passed"] is False
    assert len(rejected["errors"]) == 2


def test_passive_input_hook_normalizes_injected_mouse_and_keyboard_without_installing():
    hook = _input_hook()
    mouse = hook.mouse_event_from_raw(
        hook.WM_MOUSEWHEEL,
        time_ns=123_000_000,
        x=410,
        y=220,
        mouse_data=(0xFF88 << 16),
        flags=hook.LLMHF_INJECTED | hook.LLMHF_LOWER_IL_INJECTED,
        os_event_time_ms=77,
        foreground_hwnd=991,
        foreground_root_hwnd=991,
        window_from_point_hwnd=992,
        window_from_point_root_hwnd=991,
    )
    assert mouse == {
        "time_ns": 123_000_000,
        "device": "mouse",
        "kind": "wheel",
        "message": hook.WM_MOUSEWHEEL,
        "mutation": True,
        "injected": True,
        "lower_integrity_injected": True,
        "screen_x": 410,
        "screen_y": 220,
        "wheel_delta": -120,
        "button": "",
        "os_event_time_ms": 77,
        "foreground_hwnd": 991,
        "foreground_root_hwnd": 991,
        "window_from_point_hwnd": 992,
        "window_from_point_root_hwnd": 991,
    }
    key = hook.keyboard_event_from_raw(
        hook.WM_KEYUP,
        time_ns=124_000_000,
        vk_code=0x41,
        scan_code=30,
        flags=hook.LLKHF_INJECTED | hook.LLKHF_UP,
        os_event_time_ms=78,
        foreground_hwnd=991,
        foreground_root_hwnd=991,
        window_from_point_hwnd=992,
        window_from_point_root_hwnd=991,
    )
    assert key["kind"] == "key_up"
    assert key["mutation"] is True
    assert key["injected"] is True
    assert key["up"] is True
    assert key["vk_code"] == 0x41

    move = hook.mouse_event_from_raw(
        hook.WM_MOUSEMOVE,
        time_ns=125_000_000,
        x=411,
        y=221,
        mouse_data=0,
        flags=0,
        os_event_time_ms=79,
        foreground_hwnd=991,
        foreground_root_hwnd=991,
        window_from_point_hwnd=992,
        window_from_point_root_hwnd=991,
    )
    assert move["mutation"] is False
    assert move["injected"] is False


def test_pointer_os_evidence_requires_window_from_point_target_root():
    harness = _canary_harness()
    target_identity = _target_identity(991)
    start = {
        "job_id": "job-one",
        "decision_id": "decision-one",
        "action": "click",
        "ts": 1.0,
    }
    done = {"job_id": "job-one", "ts": 2.0}
    raw_event = {
        "hook_seq": 1,
        "time_ns": 1_500_000_000,
        "device": "mouse",
        "kind": "button_down",
        "mutation": True,
        "injected": True,
        "foreground_root_hwnd": 991,
        "window_from_point_hwnd": 992,
        "window_from_point_root_hwnd": 777,
    }

    rejected = harness._normalized_os_evidence(
        {
            "events": [raw_event],
            "hook_seq": 1,
            "dropped_event_count": 0,
            "thread_error": "",
        },
        start_sequence=0,
        target_identity=target_identity,
        starts=[start],
        dones=[done],
    )
    assert rejected["normalized_action_events"] == []
    assert rejected["unexpected_injected_target_mutations"] == [
        raw_event
    ]

    accepted_event = {
        **raw_event,
        "window_from_point_root_hwnd": 991,
    }
    accepted = harness._normalized_os_evidence(
        {
            "events": [accepted_event],
            "hook_seq": 1,
            "dropped_event_count": 0,
            "thread_error": "",
        },
        start_sequence=0,
        target_identity=target_identity,
        starts=[start],
        dones=[done],
    )
    assert len(accepted["normalized_action_events"]) == 1
    assert accepted["unexpected_injected_target_mutations"] == []
    assert (
        accepted["normalized_action_events"][0][
            "window_from_point_root_hwnd"
        ]
        == 991
    )
    wrong_foreground = {
        **accepted_event,
        "foreground_root_hwnd": 777,
        "window_from_point_root_hwnd": 777,
    }
    rejected_foreground = harness._normalized_os_evidence(
        {
            "events": [wrong_foreground],
            "hook_seq": 1,
            "dropped_event_count": 0,
            "thread_error": "",
        },
        start_sequence=0,
        target_identity=target_identity,
        starts=[start],
        dones=[done],
    )
    assert rejected_foreground["normalized_action_events"] == []
    assert rejected_foreground[
        "unexpected_injected_target_mutations"
    ] == [wrong_foreground]

    legacy_rejected = harness._legacy_os_evidence(
        {
            "events": [raw_event],
            "hook_seq": 1,
            "dropped_event_count": 0,
            "thread_error": "",
        },
        start_sequence=0,
        target_identity=target_identity,
    )
    assert legacy_rejected["normalized_action_events"] == []
    assert legacy_rejected[
        "unexpected_injected_target_mutations"
    ] == [raw_event]


@pytest.mark.parametrize(
    "callback_name,device",
    (
        ("_mouse_proc", "mouse"),
        ("_keyboard_proc", "keyboard"),
    ),
)
def test_passive_hook_callback_capture_failure_is_auditable(
    callback_name,
    device,
    monkeypatch,
):
    hook = _input_hook()
    passive = hook.PassiveInputHook()

    def failed_cast(*_args, **_kwargs):
        raise RuntimeError("synthetic capture failure")

    api = SimpleNamespace(
        call_next=lambda _n, _w, _l: 0,
    )
    monkeypatch.setattr(hook.ctypes, "cast", failed_cast)
    callback = getattr(passive, callback_name)
    assert callback(api, hook.HC_ACTION, 0, 0) == 0
    snapshot = passive.snapshot()
    assert snapshot["events"] == []
    assert snapshot["dropped_event_count"] == 1
    assert snapshot["thread_error"] == (
        f"{device} hook callback capture failed"
    )


def test_passive_hook_source_is_read_only_and_always_chains():
    hook = _input_hook()
    source = Path(hook.__file__).read_text(encoding="utf-8")
    assert "CallNextHookEx" in source
    assert "SetWindowsHookExW" in source
    assert "PostThreadMessageW" in source
    assert "SendInput" not in source
    assert "keybd_event(" not in source
    assert "mouse_event(" not in source
    assert "return api.call_next" in source


def test_external_oracle_waits_for_late_wrong_input_and_stable_snapshot(
    monkeypatch,
):
    harness = _canary_harness()
    clock = {"now": 0.0}

    def monotonic():
        return clock["now"]

    def sleep(seconds):
        clock["now"] += seconds

    def state(_url):
        events = [{"type": "scenario_success"}]
        oracle = {"passed": True, "errors": []}
        if clock["now"] >= 0.4:
            events.append({"type": "wrong_input"})
            oracle = {"passed": False, "errors": ["wrong input"]}
        return {"events": events, "oracle": oracle}

    monkeypatch.setattr(harness.time, "monotonic", monotonic)
    monkeypatch.setattr(harness.time, "sleep", sleep)
    monkeypatch.setattr(harness, "_json_get", state)
    settled, evidence = harness._settled_external_state(
        "http://example.invalid/state",
        timeout=3,
    )
    assert evidence["passed"] is True
    assert evidence["stable_seconds"] >= 1
    assert settled["oracle"]["passed"] is False
    assert [item["type"] for item in settled["events"]] == [
        "scenario_success",
        "wrong_input",
    ]


def test_profile_cleanup_is_proven(tmp_path):
    harness = _canary_harness()
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "state").write_text("x", encoding="utf-8")
    evidence = harness._remove_profile(profile)
    assert evidence["passed"] is True
    assert profile.exists() is False


def _event_factory(case_id: str, scenario: str, seed: int, page_id: str):
    events: list[dict[str, Any]] = []

    def add(event_type: str, *, trusted: bool = True, **details: Any):
        next_sequence = len(events) + 1
        details.setdefault(
            "event",
            {
                "client_x": 240 if event_type.startswith("pointer") or event_type in {"click", "dblclick", "wheel"} else None,
                "client_y": 180 if event_type.startswith("pointer") or event_type in {"click", "dblclick", "wheel"} else None,
                "screen_x": 320 if event_type.startswith("pointer") or event_type in {"click", "dblclick", "wheel"} else None,
                "screen_y": 240 if event_type.startswith("pointer") or event_type in {"click", "dblclick", "wheel"} else None,
                "button": 0 if event_type.startswith("pointer") or event_type in {"click", "dblclick"} else None,
                "buttons": 0,
                "key": details.get("key"),
                "code": details.get("code"),
                "ctrl_key": details.get("ctrl_key") is True,
                "shift_key": False,
                "alt_key": False,
                "meta_key": False,
                "repeat": details.get("repeat") is True,
                "delta_x": details.get("delta_x"),
                "delta_y": details.get("delta_y"),
                "delta_mode": 0 if event_type == "wheel" else None,
            },
        )
        details.setdefault(
            "visible_state",
            {
                "scenario": scenario,
                "revision": next_sequence,
                "final_dom_state": details.get("final_dom_state", "waiting"),
                "value": details.get("value", details.get("final_value")),
                "scroll_top": details.get("scroll_top"),
                "counts": details.get("counts"),
            },
        )
        events.append({
            "case_id": case_id,
            "scenario": scenario,
            "seed": seed,
            "page_id": page_id,
            "event_seq": next_sequence,
            "client_wall_time_ms": 1_000_000 + next_sequence,
            "client_monotonic_ms": 1000 + next_sequence,
            "type": event_type,
            "trusted": trusted,
            "details": details,
        })
        return events[-1]

    return events, add


def _click_events():
    case_id, scenario, seed = "click-case", "click_double", 4101
    page_id = f"vnext-canary:{case_id}:{scenario}:{seed}"
    events, add = _event_factory(case_id, scenario, seed, page_id)
    add("page_ready", trusted=False, final_dom_state="waiting")
    add("pointerdown", target_id="single", detail=1)
    add("pointerup", target_id="single", detail=1)
    add("click", target_id="single", detail=1)
    add("pointerdown", target_id="double", detail=1)
    add("pointerup", target_id="double", detail=1)
    add("click", target_id="double", detail=1)
    add("pointerdown", target_id="double", detail=2)
    add("pointerup", target_id="double", detail=2)
    add("click", target_id="double", detail=2)
    add("dblclick", target_id="double", detail=2)
    add(
        "scenario_success",
        succeeded=True,
        violations=[],
        final_dom_state="complete",
        counts={"single_click": 1, "double_click_events": 2, "dblclick": 1},
    )
    return events, case_id, scenario, seed, page_id


def _scroll_events():
    case_id, scenario, seed = "scroll-case", "scroll_roundtrip", 5201
    page_id = f"vnext-canary:{case_id}:{scenario}:{seed}"
    events, add = _event_factory(case_id, scenario, seed, page_id)
    add("page_ready", trusted=False, final_dom_state="waiting")
    add("wheel", direction="down", delta_y=384, wheel_count=1)
    add("scroll_state", phase="down", scroll_top=600, max_scroll=600)
    add("scroll_checkpoint", checkpoint_count=1, scroll_top=600, max_scroll=600)
    add("wheel", direction="up", delta_y=-384, wheel_count=2)
    add("scroll_state", phase="up", scroll_top=0, max_scroll=600)
    add(
        "scenario_success",
        succeeded=True,
        violations=[],
        final_dom_state="complete",
        phase="complete",
        checkpoint_count=1,
        scroll_top=0,
        wheel_count=2,
    )
    return events, case_id, scenario, seed, page_id


def _type_events():
    case_id, scenario, seed = "type-case", "type_hotkey", 6301
    page_id = f"vnext-canary:{case_id}:{scenario}:{seed}"
    token = "CANARY-ALPHA-17"
    events, add = _event_factory(case_id, scenario, seed, page_id)
    initial = "replace-this-0"
    add(
        "page_ready",
        trusted=False,
        final_dom_state="waiting",
        expected_token=token,
        initial_value=initial,
    )
    add("pointerdown", target_id="editor", detail=1)
    add("pointerup", target_id="editor", detail=1)
    add("click", target_id="editor", detail=1, click_count=1)
    add("keydown", key="Control", code="ControlLeft", ctrl_key=True, repeat=False, chord="")
    add("keydown", key="a", code="KeyA", ctrl_key=True, repeat=False, chord="ctrl+a")
    add("keyup", key="a", code="KeyA", ctrl_key=True, repeat=False, chord="ctrl+a")
    add("keyup", key="Control", code="ControlLeft", ctrl_key=False, repeat=False, chord="")
    add("beforeinput", target_id="editor", input_type="insertText", data=token, value_before=initial)
    add("input", target_id="editor", input_type="insertText", data=token, value=token)
    add("keydown", key="Control", code="ControlLeft", ctrl_key=True, repeat=False, chord="")
    add("keydown", key="Enter", code="Enter", ctrl_key=True, repeat=False, chord="ctrl+enter")
    add(
        "scenario_success",
        succeeded=True,
        violations=[],
        final_dom_state="complete",
        final_value=token,
        expected_token=token,
        select_all_count=1,
        submit_count=1,
        click_count=1,
    )
    add("keyup", key="Enter", code="Enter", ctrl_key=True, repeat=False, chord="ctrl+enter")
    add("keyup", key="Control", code="ControlLeft", ctrl_key=False, repeat=False, chord="")
    return events, case_id, scenario, seed, page_id


@pytest.mark.parametrize(
    "factory",
    (_click_events, _scroll_events, _type_events),
    ids=("click-double", "scroll-roundtrip", "type-hotkey"),
)
def test_independent_oracle_accepts_only_complete_trusted_exact_sequences(factory):
    oracle = _oracle()
    events, case_id, scenario, seed, page_id = factory()
    verdict = oracle.validate_case_events(
        events,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    assert verdict["passed"] is True, verdict
    assert verdict["wrong_input_count"] == 0
    assert verdict["success_event_count"] == 1
    expected_actions = {
        "click_double": ["click", "double_click"],
        "scroll_roundtrip": ["scroll", "scroll"],
        "type_hotkey": ["click", "hotkey", "type_text", "hotkey"],
    }
    observed_actions = [
        item["action"] for item in verdict["action_observations"]
    ]
    if scenario == "scroll_roundtrip":
        assert len(observed_actions) >= 2
        assert set(observed_actions) == {"scroll"}
    else:
        assert observed_actions == expected_actions[scenario]
    assert all(
        item["decidable"] is True
        and item["actual_changed"] is True
        and item["before_state_sha256"] != item["after_state_sha256"]
        for item in verdict["action_observations"]
    )

    corrupted = json.loads(json.dumps(events))
    trusted_index = next(
        index for index, item in enumerate(corrupted)
        if item["type"] not in {"page_ready"}
    )
    corrupted[trusted_index]["trusted"] = False
    rejected = oracle.validate_case_events(
        corrupted,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    assert rejected["passed"] is False
    assert any("untrusted input event" in error for error in rejected["errors"])

    duplicate = json.loads(json.dumps(events))
    duplicate.insert(-1, {
        "case_id": case_id,
        "scenario": scenario,
        "seed": seed,
        "page_id": page_id,
        "event_seq": 0,
        "type": "wrong_input",
        "trusted": True,
        "details": {"code": "duplicate"},
    })
    for index, item in enumerate(duplicate, start=1):
        item["event_seq"] = index
    rejected = oracle.validate_case_events(
        duplicate,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    assert rejected["passed"] is False
    assert rejected["wrong_input_count"] == 1


def test_type_oracle_rejects_correction_or_non_exact_value_progression():
    oracle = _oracle()
    events, case_id, scenario, seed, page_id = _type_events()
    input_event = next(item for item in events if item["type"] == "input")
    input_event["details"]["input_type"] = "deleteContentBackward"
    verdict = oracle.validate_case_events(
        events,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    assert verdict["passed"] is False
    assert any(
        "deletion, correction" in error
        for error in verdict["errors"]
    )


def test_type_oracle_rejects_any_extra_trusted_key_pair():
    oracle = _oracle()
    events, case_id, scenario, seed, page_id = _type_events()
    insert_at = next(
        index for index, item in enumerate(events)
        if item["type"] == "beforeinput"
    )
    extras = [
        {
            **events[insert_at - 1],
            "type": "keydown",
            "details": {
                **events[insert_at - 1]["details"],
                "key": "ArrowRight",
                "code": "ArrowRight",
                "ctrl_key": False,
                "chord": "",
            },
        },
        {
            **events[insert_at - 1],
            "type": "keyup",
            "details": {
                **events[insert_at - 1]["details"],
                "key": "ArrowRight",
                "code": "ArrowRight",
                "ctrl_key": False,
                "chord": "",
            },
        },
    ]
    events[insert_at:insert_at] = extras
    for index, item in enumerate(events, start=1):
        item["event_seq"] = index
        item["client_wall_time_ms"] = 1_000_000 + index
        item["client_monotonic_ms"] = 1000 + index
    verdict = oracle.validate_case_events(
        events,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    assert verdict["passed"] is False
    assert any("sequence is not exact" in error for error in verdict["errors"])


def _client_result_for_trace(path: Path, run_id: str) -> dict[str, Any]:
    return {
        "result": {
            "run_id": run_id,
            "outcome": "terminal",
            "status": "done",
            "runtime": {
                "current_run": {
                    "run_id": run_id,
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                },
            },
        },
        "trace_binding": {"trace_file": str(path), "passed": True},
        "execution_valid": True,
        "passed": True,
    }


def _target_identity(hwnd: int) -> dict[str, Any]:
    return {
        "hwnd": hwnd,
        "root_hwnd": hwnd,
        "title": "WinPilot vNext Canary test",
        "class_name": "Chrome_WidgetWin_1",
        "process_id": 4242,
        "executable": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "rect": [80, 60, 1180, 820],
    }


def test_runtime_evidence_is_rebuilt_from_private_journal_and_anchor(
    monkeypatch,
):
    harness = _canary_harness()
    run_id = "arp-evidence-run"
    mission_id = "mission-evidence-run"
    report_wire = {
        "format": "runtime-evidence-report/1",
        "mission_id": mission_id,
        "run_id": run_id,
        "status": "complete",
        "actor_decisions": [],
        "admissions": [],
        "physical_attempts": [],
        "action_bindings": [],
        "frame_artifacts": [],
        "journal_head_sha256": "a" * 64,
        "journal_event_count": 7,
        "missing_evidence": [],
        "verification_failures": [],
    }

    class Status:
        value = "complete"

    class Report:
        def __init__(self):
            self.mission_id = mission_id
            self.run_id = run_id
            self.journal_head_sha256 = "a" * 64
            self.journal_event_count = 7
            self.status = Status()
            self.missing_evidence = ()
            self.verification_failures = ()

        def to_dict(self):
            return json.loads(json.dumps(report_wire))

    calls: list[tuple[Path, str, str, str | None]] = []

    def rebuild(path, observed_mission, observed_run, expected_journal_head_sha256=None):
        calls.append(
            (
                Path(path),
                observed_mission,
                observed_run,
                expected_journal_head_sha256,
            )
        )
        return Report()

    monkeypatch.setattr(
        harness.RuntimeEvidencePlane,
        "rebuild_report",
        staticmethod(rebuild),
    )
    anchor = {
        "type": "runtime_evidence_sealed",
        "ts": 1_721_000_000.25,
        "runtime_version": "vnext",
        "no_legacy_fallback": True,
        "mission_id": mission_id,
        "run_id": run_id,
        "journal_head_sha256": "a" * 64,
        "journal_event_count": 7,
        "report_sha256": harness.canonical_sha256(report_wire),
        "report_status": "complete",
        "missing_evidence_count": 0,
        "verification_failure_count": 0,
    }
    rebuilt, safe_anchor = harness._rebuild_runtime_evidence(
        [anchor],
        run_id=run_id,
    )
    assert rebuilt == report_wire
    assert safe_anchor["report_sha256"] == anchor["report_sha256"]
    assert safe_anchor["type"] == "runtime_evidence_sealed"
    assert safe_anchor["ts"] == anchor["ts"]
    assert calls == [
        (
            harness.RUNTIME_EVIDENCE_ROOT / run_id,
            mission_id,
            run_id,
            "a" * 64,
        )
    ]

    tampered = dict(anchor)
    tampered["report_sha256"] = "b" * 64
    with pytest.raises(RuntimeError, match="report_sha256"):
        harness._rebuild_runtime_evidence([tampered], run_id=run_id)


def test_action_reconciliation_uses_mission_terminal_status_not_queue_done(
    monkeypatch,
):
    harness = _canary_harness()
    run_id = "arp-terminal-binding"
    terminal = {
        "type": "vnext_canary_done",
        "run_id": run_id,
        "status": "mission_verified",
    }
    queue_done = {
        "type": "desktop_action_queue",
        "state": "done",
        "run_id": run_id,
        "status": "queue_completed",
    }
    events = [
        {
            "type": "vnext_canary_start",
            "run_id": run_id,
            "target": {
                "binding_id": "binding-1",
                "hwnd": 100,
                "root_hwnd": 100,
                "process_id": 200,
            },
        },
        terminal,
    ]
    monkeypatch.setattr(
        harness,
        "_trace_sources",
        lambda _client: (events, [], [queue_done], [], [], "trace.jsonl"),
    )
    monkeypatch.setattr(
        harness,
        "_normalized_os_evidence",
        lambda *_args, **_kwargs: {"normalized_action_events": []},
    )
    monkeypatch.setattr(
        harness,
        "_rebuild_runtime_evidence",
        lambda *_args, **_kwargs: ({"report": "rebuilt"}, {"anchor": "safe"}),
    )
    monkeypatch.setattr(
        harness,
        "_normalized_browser_events",
        lambda _state: [],
    )
    monkeypatch.setattr(
        harness,
        "_bind_browser_events_to_runtime_report",
        lambda *_args, **_kwargs: [],
    )
    observed: dict[str, Any] = {}

    def reconcile(**kwargs):
        observed.update(kwargs)
        return {"passed": True}

    monkeypatch.setattr(
        harness.campaign_state,
        "reconcile_action_evidence",
        reconcile,
    )
    reconciliation, _os, _trace, _anchor = (
        harness._reconcile_vnext_actions(
            sample={
                "sample_key": "001:click:vnext",
                "runtime": "vnext",
            },
            attempt=1,
            client_result={
                "result": {
                    "run_id": run_id,
                }
            },
            external_state={"oracle": {"passed": False}},
            external_oracle_passed=False,
            hook_snapshot={},
            hook_start_sequence=0,
            target_identity={
                "hwnd": 100,
                "root_hwnd": 100,
                "process_id": 200,
            },
        )
    )

    assert reconciliation == {"passed": True}
    assert observed["runtime_terminal_status"] == "mission_verified"
    assert observed["external_oracle_passed"] is False


_PRIVATE_PROJECTION_FIELDS = frozenset(
    {
        "actor_envelope",
        "broker_projection",
        "canonical_arguments",
        "controller_transition",
        "exact_text",
        "full_model_response",
        "hotkey_chord",
        "model_response",
        "physical_arguments",
        "prompt",
        "raw_action_receipt",
        "raw_model_response",
        "reasoning",
        "submitted_arguments",
        "tool_schema",
    }
)
_PRIVATE_EVIDENCE_SURFACES = frozenset(
    {
        "dedicated_runtime_evidence_journal",
        "non_public_audit_artifact",
        "runtime_evidence_report",
    }
)


def _privacy_fixture(harness) -> dict[str, Any]:
    exact_text = "PRIVATE-TEXT-campaign-test-7f2d"
    hotkey = ["ctrl", "shift", "f12"]
    click = {
        "image_x": 913.125,
        "image_y": 426.875,
        "physical_x": 19_137,
        "physical_y": 14_261,
    }
    run_id = "arp-private-evidence-fixture"
    mission_id = "mission-private-evidence-fixture"
    obligation_id = "full-goal"
    target = {
        "binding_id": "binding-private-fixture",
        "hwnd": 991,
        "process_id": 4102,
        "executable_identity": "private-fixture-browser.exe",
        "epoch": 4,
    }
    frame_region = [0, 0, 1280, 720]
    action_specs = [
        (
            "type_text",
            {"text": exact_text},
            {"kind": "type_text", "text": exact_text},
        ),
        (
            "hotkey",
            {"keys": hotkey},
            {
                "kind": "hotkey",
                "modifiers": hotkey[:-1],
                "key": hotkey[-1],
            },
        ),
        (
            "click",
            {
                "image_x": click["image_x"],
                "image_y": click["image_y"],
            },
            {
                "kind": "pointer_click",
                "x": click["physical_x"],
                "y": click["physical_y"],
                "button": "left",
                "click_count": 1,
            },
        ),
    ]
    actor_decisions: list[dict[str, Any]] = []
    physical_attempts: list[dict[str, Any]] = []
    actor_decisions_by_action: dict[str, dict[str, Any]] = {}
    physical_attempts_by_action: dict[str, dict[str, Any]] = {}

    tool_schemas = {
        "type_text": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 4096,
                }
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        "hotkey": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 5,
                    "uniqueItems": True,
                }
            },
            "required": ["keys"],
            "additionalProperties": False,
        },
        "click": {
            "type": "object",
            "properties": {
                "image_x": {"type": "number", "minimum": 0},
                "image_y": {"type": "number", "minimum": 0},
            },
            "required": ["image_x", "image_y"],
            "additionalProperties": False,
        },
    }

    for index, (action, arguments, physical) in enumerate(
        action_specs,
        start=1,
    ):
        projection_material = {
            "format": "broker-projection/1",
            "mission_id": mission_id,
            "run_id": run_id,
            "catalog_fingerprint": "2" * 64,
            "profile_id": "fixed-standard",
            "state_fingerprint": "3" * 64,
            "mission_revision": 0,
            "obligation_id": obligation_id,
            "turn_index": index - 1,
            "execution_route": "visual_actor",
            "executor_id": None,
            "target_binding_id": target["binding_id"],
            "frame_id": index,
            "frame_scope": "target_window",
            "pointer_lease_id": None,
            "tool_names": [action],
            "active_grant_ids": [],
            "missing_capabilities": [],
            "projection_reasons": [],
        }
        projection_id = (
            "projection-" + canonical_sha256(projection_material)[:24]
        )
        broker_projection = {
            "format": projection_material["format"],
            "projection_id": projection_id,
            "mission_id": projection_material["mission_id"],
            "run_id": projection_material["run_id"],
            "catalog_fingerprint": projection_material[
                "catalog_fingerprint"
            ],
            "profile_id": projection_material["profile_id"],
            "state_fingerprint": projection_material["state_fingerprint"],
            "mission_revision": projection_material["mission_revision"],
            "obligation_id": projection_material["obligation_id"],
            "turn_index": projection_material["turn_index"],
            "execution_route": projection_material["execution_route"],
            "executor_id": projection_material["executor_id"],
            "target_binding_id": projection_material["target_binding_id"],
            "frame_id": projection_material["frame_id"],
            "frame_scope": projection_material["frame_scope"],
            "pointer_lease_id": projection_material["pointer_lease_id"],
            "tool_names": projection_material["tool_names"],
            "active_grants": [],
            "missing_capabilities": projection_material[
                "missing_capabilities"
            ],
            "projection_reasons": projection_material[
                "projection_reasons"
            ],
        }
        envelope_material = {
            "format": "actor-envelope/1",
            "mission_id": mission_id,
            "run_id": run_id,
            "mission_revision": 0,
            "mission_projection_sha256": "1" * 64,
            "accepted_mission_event_ids": ["event-target-bound"],
            "obligation_id": obligation_id,
            "turn_index": index - 1,
            "catalog_fingerprint": "2" * 64,
            "profile_id": "fixed-standard",
            "state_fingerprint": "3" * 64,
            "frame_id": index,
            "frame_epoch": 4,
            "frame_hwnd": target["hwnd"],
            "frame_scope": "target_window",
            "frame_region": copy.deepcopy(frame_region),
            "submitted_image_size": [1280, 720],
            "frame_image_sha256": f"{index:064x}",
            "target_binding_id": target["binding_id"],
            "pointer_disposition": "released",
            "pointer_lease_id": None,
            "broker_projection_id": projection_id,
            "capability_grant_ids": [],
            "allowed_evidence_scope": ["evidence-current-frame"],
            "proposal": {
                "format": "actor-proposal/0",
                "kind": "desktop_action",
                "action": action,
                "args": copy.deepcopy(arguments),
                "local_intent": "perform one bounded desktop action",
            },
        }
        decision_id = (
            "decision-" + canonical_sha256(envelope_material)[:24]
        )
        envelope = ActorDecisionEnvelope.from_dict(
            {
                "decision_id": decision_id,
                **envelope_material,
            }
        ).to_dict()
        assert ActorDecisionEnvelope.from_dict(envelope).to_dict() == envelope
        tool_schema = copy.deepcopy(tool_schemas[action])
        decision = ActorDecisionRecord.from_dict(
            {
                "format": "actor-decision-record/1",
                "decision_id": decision_id,
                "mission_id": mission_id,
                "run_id": run_id,
                "mission_revision": 0,
                "obligation_id": obligation_id,
                "turn_index": index - 1,
                "proposal_kind": "desktop_action",
                "action": action,
                "canonical_arguments": copy.deepcopy(arguments),
                "canonical_arguments_sha256": canonical_sha256(arguments),
                "actor_envelope": envelope,
                "actor_envelope_sha256": canonical_sha256(envelope),
                "broker_projection": broker_projection,
                "broker_projection_sha256": canonical_sha256(
                    broker_projection
                ),
                "tool_schema": tool_schema,
                "tool_schema_sha256": canonical_sha256(tool_schema),
            }
        ).to_dict()
        assert ActorDecisionRecord.from_dict(decision).to_dict() == decision
        frame_identity = {
            "frame_id": index,
            "epoch": 4,
            "hwnd": target["hwnd"],
            "region": copy.deepcopy(frame_region),
            "scope": "target_window",
            "submitted_size": [1280, 720],
            "image_sha256": envelope["frame_image_sha256"],
            "media_type": "image/jpeg",
        }
        admission_identity = {
            "decision_id": decision_id,
            "actor_decision_record_sha256": canonical_sha256(decision),
            "frame_identity": frame_identity,
            "target_identity": target,
            "mutation_permit_id": f"permit-private-{index}",
        }
        physical_attempt = PhysicalAttemptRecord.create(
            mission_id=mission_id,
            run_id=run_id,
            obligation_id=obligation_id,
            decision_id=decision_id,
            action_id=decision_id,
            action=action,
            attempt_index=1,
            submitted_arguments=copy.deepcopy(arguments),
            physical_arguments=copy.deepcopy(physical),
            raw_device_route=f"raw_device.admitted_physical_{action}/0",
            mechanism="physical_sendinput",
            target_identity=copy.deepcopy(target),
            frame_identity=copy.deepcopy(frame_identity),
            mutation_permit_id=f"permit-private-{index}",
            actor_decision_record_sha256=canonical_sha256(decision),
            admission_record_sha256=canonical_sha256(admission_identity),
            timestamp_ns=10_000 + index,
        ).to_dict()
        assert (
            PhysicalAttemptRecord.from_dict(physical_attempt).to_dict()
            == physical_attempt
        )
        actor_decisions.append(decision)
        physical_attempts.append(physical_attempt)
        actor_decisions_by_action[action] = decision
        physical_attempts_by_action[action] = physical_attempt

    actor_decisions.sort(key=lambda item: item["decision_id"])
    physical_attempts.sort(key=lambda item: item["decision_id"])
    report = RuntimeEvidenceReport.from_dict(
        {
            "format": harness.campaign_state.RUNTIME_EVIDENCE_REPORT_FORMAT,
            "mission_id": mission_id,
            "run_id": run_id,
            "status": "incomplete",
            "actor_decisions": actor_decisions,
            "admissions": [],
            "physical_attempts": physical_attempts,
            "action_bindings": [],
            "frame_artifacts": [],
            "journal_head_sha256": "a" * 64,
            "journal_event_count": (
                len(actor_decisions) + len(physical_attempts)
            ),
            "missing_evidence": ["private-fixture-is-read-only"],
            "verification_failures": [],
        }
    ).to_dict()
    assert RuntimeEvidenceReport.from_dict(report).to_dict() == report
    return {
        "text": exact_text,
        "hotkey": hotkey,
        "hotkey_chord": "+".join(hotkey),
        "click": click,
        "report": report,
        "report_sha256": canonical_sha256(report),
        "decisions": actor_decisions_by_action,
        "attempts": physical_attempts_by_action,
        "decision_ids": {
            action: decision["decision_id"]
            for action, decision in actor_decisions_by_action.items()
        },
        "journal": {
            "format": "runtime-evidence-journal-event/1",
            "event_kind": "actor_decision",
            "payload": actor_decisions_by_action["type_text"],
        },
        "audit_artifact": {
            "format": "runtime-evidence-private-audit/1",
            "actor_envelope": actor_decisions_by_action["type_text"][
                "actor_envelope"
            ],
            "physical_attempt": physical_attempts_by_action["type_text"],
        },
    }


def _assert_public_json_projection(
    surface_name: str,
    value: Any,
    *,
    private: dict[str, Any],
) -> Any:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )
    decoded = json.loads(encoded)
    forbidden_scalars = {
        private["text"],
        private["hotkey_chord"],
        private["click"]["image_x"],
        private["click"]["image_y"],
        private["click"]["physical_x"],
        private["click"]["physical_y"],
    }

    def visit(item: Any, path: str) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                assert key not in _PRIVATE_PROJECTION_FIELDS, (
                    f"{surface_name}{path} exposes private field {key!r}"
                )
                visit(nested, f"{path}.{key}")
            return
        if isinstance(item, list):
            assert item != private["hotkey"], (
                f"{surface_name}{path} exposes the private hotkey"
            )
            for index, nested in enumerate(item):
                visit(nested, f"{path}[{index}]")
            return
        assert not any(
            type(item) is type(secret) and item == secret
            for secret in forbidden_scalars
        ), f"{surface_name}{path} exposes a private scalar value"

    visit(decoded, "$")
    return decoded


def _serialize_private_evidence_surface(
    surface_name: str,
    value: Any,
) -> Any:
    assert surface_name in _PRIVATE_EVIDENCE_SURFACES
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )


def test_campaign_artifact_projections_do_not_copy_private_action_payloads():
    harness = _canary_harness()
    private = _privacy_fixture(harness)
    external_state = {
        "events": [
            {
                "type": "input",
                "trusted": True,
                "details": {"data": private["text"]},
            },
            {
                "type": "keydown",
                "trusted": True,
                "details": {
                    "keys": private["hotkey"],
                    "chord": private["hotkey_chord"],
                },
            },
            {
                "type": "pointerdown",
                "trusted": True,
                "details": {
                    "screen_x": private["click"]["image_x"],
                    "screen_y": private["click"]["image_y"],
                },
            },
        ],
        "oracle": {
            "source": "independent_vnext_canary_browser_oracle",
            "passed": False,
            "case_id": "case-one",
            "scenario": "type_hotkey",
            "seed": 6301,
            "page_id": "private-page",
            "wrong_input_count": 1,
            "success_event_count": 0,
            "action_observations": [
                {
                    "action": "type_text",
                    "start_event_seq": 1,
                    "end_event_seq": 1,
                    "event_types": ["input"],
                    "before_state": {"value": ""},
                    "after_state": {"value": private["text"]},
                    "before_state_sha256": "a" * 64,
                    "after_state_sha256": "b" * 64,
                    "decidable": True,
                    "actual_changed": True,
                    "errors": [private["text"]],
                }
            ],
            "errors": [private["hotkey_chord"]],
        },
    }
    safe_oracle = harness._safe_external_oracle(external_state, {})
    safe_os = harness._safe_os_input_evidence(
        {
            "raw_events": [
                {
                    "kind": "key_down",
                    "text": private["text"],
                    "keys": private["hotkey"],
                },
                {
                    "kind": "button_down",
                    "x": private["click"]["image_x"],
                    "y": private["click"]["image_y"],
                },
            ],
            "normalized_action_events": [
                {
                    "type": "keydown",
                    "action": "hotkey",
                    "decision_id": private["decision_ids"]["hotkey"],
                    "sequence": 1,
                    "keys": private["hotkey"],
                },
                {
                    "type": "pointerdown",
                    "action": "click",
                    "decision_id": private["decision_ids"]["click"],
                    "sequence": 2,
                    "x": private["click"]["image_x"],
                    "y": private["click"]["image_y"],
                },
            ],
            "external_untrusted_mutations": [],
            "unexpected_injected_target_mutations": [],
            "dropped_event_count": 0,
            "hook_seq": 2,
            "thread_error": "",
            "input_release_audit": {
                "passed": True,
                "key_balance": {},
                "button_balance": {},
                "errors": [],
            },
        }
    )
    raw_trace_audit = {
        "passed": False,
        "trace_sha256": "d" * 64,
        "trace_event_count": 3,
        "run_id": private["report"]["run_id"],
        "runtime_version": "vnext",
        "outer_start_count": 1,
        "model_event_types": ["vnext_model_call"],
        "model_call_count": 1,
        "models": ["model-one"],
        "target_expectation": {
            "class_name": "Chrome_WidgetWin_1",
            "executable_identity_sha256": "e" * 64,
            "hwnd": 991,
            "process_id": 4102,
            "region": [0, 0, 1280, 720],
            "root_hwnd": 991,
            "private_title": private["text"],
        },
        "target_binding_id": "binding-private-fixture",
        "turn_count": 1,
        "done_count": 0,
        "runtime_evidence_anchor_count": 1,
        "receipt_summary": {
            "count": 3,
            "controller_actions": ["type_text", "hotkey", "click"],
            "decision_ids": [
                item["decision_id"]
                for item in private["report"]["actor_decisions"]
            ],
            "target_binding_ids": ["binding-private-fixture"] * 3,
            "outcomes": ["applied", "applied", "applied"],
            "run_ids": [private["report"]["run_id"]] * 3,
            "target_hwnds": [991, 991, 991],
            "actor_envelope": private["decisions"]["type_text"][
                "actor_envelope"
            ],
        },
        "forensic_summary": {
            "main_request_count": 1,
            "main_provider_attempt_count": 1,
            "done_judge_request_count": 0,
            "errors": [private["hotkey_chord"]],
        },
        "errors": [private["text"]],
        "canonical_arguments": {
            "text": private["text"],
            "keys": private["hotkey"],
        },
        "physical_attempt": private["attempts"]["type_text"],
        "safety_abort_reasons": [],
    }
    safe_trace = harness._safe_trace_audit(raw_trace_audit)
    runtime_anchor = {
        "type": "runtime_evidence_sealed",
        "ts": 1_721_000_000.25,
        "runtime_version": "vnext",
        "no_legacy_fallback": True,
        "mission_id": private["report"]["mission_id"],
        "run_id": private["report"]["run_id"],
        "journal_head_sha256": private["report"]["journal_head_sha256"],
        "journal_event_count": private["report"]["journal_event_count"],
        "report_sha256": private["report_sha256"],
        "report_status": private["report"]["status"],
        "missing_evidence_count": len(private["report"]["missing_evidence"]),
        "verification_failure_count": 0,
    }
    safe_attempt = harness._safe_attempt_result(
        {
            "sample_key": "001:type-hotkey:vnext",
            "execution_order": 1,
            "attempt": 1,
            "case": "type-hotkey",
            "family": "type_hotkey",
            "scenario": "type_hotkey",
            "seed": 6301,
            "runtime_version": "vnext",
            "runtime_budget": harness.FROZEN_RUNTIME_BUDGET,
            "frozen_config_sha256": "c" * 64,
            "started_at": "2026-07-27T12:00:00Z",
            "finished_at": "2026-07-27T12:01:00Z",
            "status": "failed",
            "passed": False,
            "trace_audit": raw_trace_audit,
            "external_settlement": {
                "passed": False,
                "polls": 1,
                "stable_seconds": 0.0,
                "errors": [private["text"]],
            },
            "config_audit": {
                "passed": True,
                "config_sha256": "c" * 64,
                "model": "model-one",
                "provider": "provider-one",
                "adapter": "adapter-one",
                "api_max_retries": 0,
                "prompt": private["text"],
            },
            "target_source_audit": {
                "passed": True,
                "served_sha256": "d" * 64,
                "source_sha256": "d" * 64,
                "private_note": private["hotkey_chord"],
            },
            "target_identity_audit": {
                "passed": True,
                "before": {"title": private["text"], "hwnd": 100},
                "after": {"title": private["text"], "hwnd": 100},
                "errors": [],
                "private_note": private["hotkey_chord"],
            },
            "runner_error": private["text"],
            "runtime_evidence_error": private["hotkey_chord"],
            "cleanup": {
                "passed": False,
                "errors": [private["text"]],
            },
            "client_result": {
                "execution_valid": False,
                "passed": False,
                "result": {
                    "run_id": private["report"]["run_id"],
                    "outcome": "error",
                    "status": "failed",
                    "runtime": {
                        "current_run": {
                            "runtime_version": "vnext",
                            "no_legacy_fallback": True,
                        }
                    },
                },
            },
        },
        external_oracle=safe_oracle,
        os_input_evidence=safe_os,
        reconciliation=None,
        runtime_evidence_anchor=runtime_anchor,
    )
    ordinary_error = harness._invalid_suite_result(
        campaign_dir=Path("campaign-private-fixture"),
        code="synthetic_private_error",
        error=RuntimeError(private["text"]),
    )
    campaign_summary = harness.campaign_state.with_content_hash(
        {
            "schema_version": "campaign-privacy-summary/1",
            "sample_key": safe_attempt["sample_key"],
            "status": safe_attempt["status"],
            "runtime_version": safe_attempt["runtime_version"],
            "runtime_evidence_report_sha256": private["report_sha256"],
            "trusted_action_event_count": safe_os[
                "trusted_action_event_count"
            ],
        }
    )
    final_report = harness.campaign_state.with_content_hash(
        {
            "schema_version": "campaign-public-report/1",
            "summary": campaign_summary,
            "attempt": safe_attempt,
            "external_oracle": safe_oracle,
            "os_input_evidence": safe_os,
            "runtime_evidence_report_sha256": private["report_sha256"],
        }
    )
    public_surfaces = {
        "ordinary_trace": safe_trace,
        "bus_websocket_event": {
            "type": "vnext_canary_turn",
            "run_id": private["report"]["run_id"],
            "decision_id": private["decision_ids"]["type_text"],
            "runtime_evidence_report_sha256": private["report_sha256"],
            "trace_audit": safe_trace,
        },
        "api_run_projection": {
            **safe_attempt["client_projection"],
            "runtime_evidence_report_status": runtime_anchor["report_status"],
            "runtime_evidence_report_sha256": private["report_sha256"],
        },
        "campaign_summary": campaign_summary,
        "ui_result": {
            "status": safe_attempt["status"],
            "passed": safe_attempt["passed"],
            "runtime_version": safe_attempt["runtime_version"],
            "error_identity": ordinary_error["error_identity"],
        },
        "ordinary_error": ordinary_error,
        "final_non_private_report": final_report,
        "external_oracle_artifact": safe_oracle,
        "os_input_artifact": safe_os,
        "attempt_result_artifact": safe_attempt,
    }
    for surface_name, value in public_surfaces.items():
        _assert_public_json_projection(
            surface_name,
            value,
            private=private,
        )

    private_report = _serialize_private_evidence_surface(
        "runtime_evidence_report",
        private["report"],
    )
    private_journal = _serialize_private_evidence_surface(
        "dedicated_runtime_evidence_journal",
        private["journal"],
    )
    private_audit = _serialize_private_evidence_surface(
        "non_public_audit_artifact",
        private["audit_artifact"],
    )
    private_type_decision = next(
        item
        for item in private_report["actor_decisions"]
        if item["action"] == "type_text"
    )
    private_type_attempt = next(
        item
        for item in private_report["physical_attempts"]
        if item["action"] == "type_text"
    )
    assert private_type_decision["canonical_arguments"] == {
        "text": private["text"]
    }
    assert private_type_attempt["physical_arguments"] == {
        "kind": "type_text",
        "text": private["text"],
    }
    assert private_journal["payload"]["actor_envelope"]["proposal"]["args"] == {
        "text": private["text"]
    }
    assert private_audit["physical_attempt"]["submitted_arguments"] == {
        "text": private["text"]
    }


def test_read_only_runtime_evidence_report_is_an_allowed_private_surface():
    harness = _canary_harness()
    private = _privacy_fixture(harness)

    serialized = _serialize_private_evidence_surface(
        "runtime_evidence_report",
        private["report"],
    )
    canonical_report = RuntimeEvidenceReport.from_dict(serialized).to_dict()

    assert canonical_report == serialized
    for decision in canonical_report["actor_decisions"]:
        assert (
            ActorDecisionEnvelope.from_dict(
                decision["actor_envelope"]
            ).to_dict()
            == decision["actor_envelope"]
        )
        assert ActorDecisionRecord.from_dict(decision).to_dict() == decision
    for attempt in canonical_report["physical_attempts"]:
        assert (
            PhysicalAttemptRecord.from_dict(attempt).to_dict()
            == attempt
        )
    type_text = next(
        item
        for item in canonical_report["actor_decisions"]
        if item["action"] == "type_text"
    )
    assert type_text["canonical_arguments"]["text"] == private["text"]


@pytest.mark.parametrize(
    "field_name",
    sorted(_PRIVATE_PROJECTION_FIELDS),
)
def test_public_projection_rejects_every_private_evidence_field(field_name):
    harness = _canary_harness()
    private = _privacy_fixture(harness)

    with pytest.raises(AssertionError, match=field_name):
        _assert_public_json_projection(
            "parameterized_public_projection",
            {"status": "failed", field_name: {"value": "not-public"}},
            private=private,
        )


@pytest.mark.parametrize(
    "secret_name",
    [
        "text",
        "hotkey",
        "hotkey_chord",
        "image_x",
        "image_y",
        "physical_x",
        "physical_y",
    ],
)
def test_public_projection_rejects_every_private_action_value(secret_name):
    harness = _canary_harness()
    private = _privacy_fixture(harness)
    secrets = {
        "text": private["text"],
        "hotkey": private["hotkey"],
        "hotkey_chord": private["hotkey_chord"],
        "image_x": private["click"]["image_x"],
        "image_y": private["click"]["image_y"],
        "physical_x": private["click"]["physical_x"],
        "physical_y": private["click"]["physical_y"],
    }

    with pytest.raises(AssertionError, match="private"):
        _assert_public_json_projection(
            "parameterized_public_projection",
            {"status": "failed", "opaque_value": secrets[secret_name]},
            private=private,
        )


def test_public_projection_allows_runtime_evidence_report_identifiers():
    harness = _canary_harness()
    private = _privacy_fixture(harness)

    projected = _assert_public_json_projection(
        "api_run_projection",
        {
            "runtime_evidence_report": {
                "sha256": private["report_sha256"],
                "status": "incomplete",
                "missing_evidence_count": 1,
            },
            "runtime_evidence_report_sha256": private["report_sha256"],
            "runtime_evidence_report_status": "incomplete",
            "runtime_evidence_report_missing_count": 1,
        },
        private=private,
    )

    assert projected["runtime_evidence_report_sha256"] == private[
        "report_sha256"
    ]
    assert projected["runtime_evidence_report"] == {
        "sha256": private["report_sha256"],
        "status": "incomplete",
        "missing_evidence_count": 1,
    }
    assert harness.campaign_state._private_evidence_field(projected) == ""


def test_public_projection_rejects_canonical_arguments():
    harness = _canary_harness()
    private = _privacy_fixture(harness)
    leaked = {
        "status": "failed",
        "canonical_arguments": {"text": private["text"]},
    }

    with pytest.raises(AssertionError, match="canonical_arguments"):
        _assert_public_json_projection(
            "api_run_projection",
            leaked,
            private=private,
        )

    assert (
        harness.campaign_state._private_evidence_field(leaked)
        == "canonical_arguments"
    )


def test_trace_audit_binds_frozen_vnext_and_rejects_legacy_or_fallback(tmp_path):
    harness = _canary_harness()
    assert harness.VNEXT_MODEL_ROLES == frozenset({
        VISUAL_ACTOR_MODEL_ROLE,
        VISUAL_VERIFIER_MODEL_ROLE,
    })
    run_id = "arp-vnext-canary"
    target_hwnd = 991
    target_identity = _target_identity(target_hwnd)
    expected_target = harness._expected_target_trace(target_identity)
    mission_id = "mission-one"
    events = [
        {
            "type": "arp_run_start",
            "run_id": run_id,
            "runtime_version": "vnext",
            "graph_name": harness.CANARY_TASK,
            "task_contract": None,
        },
        {
            "type": "vnext_canary_start",
            "run_id": run_id,
            "mission_id": mission_id,
            "runtime_budget": dict(harness.FROZEN_RUNTIME_BUDGET),
            "no_legacy_fallback": True,
            "model": "model-one",
            "target": {
                "binding_id": "binding-1",
                **expected_target,
            },
        },
        {
            "type": "vnext_model_call",
            "run_id": run_id,
            "mission_id": mission_id,
            "model": "model-one",
            "model_role": VISUAL_ACTOR_MODEL_ROLE,
        },
        {
            "type": "vnext_model_call",
            "run_id": run_id,
            "mission_id": mission_id,
            "model": "model-one",
            "model_role": VISUAL_ACTOR_MODEL_ROLE,
        },
        {
            "type": "vnext_model_call",
            "run_id": run_id,
            "mission_id": mission_id,
            "model": "model-one",
            "model_role": VISUAL_ACTOR_MODEL_ROLE,
        },
        {
            "type": "vnext_model_call",
            "run_id": run_id,
            "mission_id": mission_id,
            "model": "model-one",
            "model_role": VISUAL_VERIFIER_MODEL_ROLE,
        },
        {
            "type": "vnext_raw_receipt",
            "metadata": {
                "run_id": run_id,
                "decision_id": "decision-1",
                "controller_action": "click",
                "target_hwnd": target_hwnd,
                "target_binding_id": "binding-1",
            },
            "receipt": {"outcome": "applied"},
        },
        {
            "type": "vnext_raw_receipt",
            "metadata": {
                "run_id": run_id,
                "decision_id": "decision-2",
                "controller_action": "double_click",
                "target_hwnd": target_hwnd,
                "target_binding_id": "binding-1",
            },
            "receipt": {"outcome": "applied"},
        },
        {
            "type": "vnext_canary_turn",
            "run_id": run_id,
            "mission_id": mission_id,
            "no_legacy_fallback": True,
            "turn_index": 0,
        },
        {
            "type": "vnext_canary_turn",
            "run_id": run_id,
            "mission_id": mission_id,
            "no_legacy_fallback": True,
            "turn_index": 1,
        },
        {
            "type": "vnext_canary_turn",
            "run_id": run_id,
            "mission_id": mission_id,
            "no_legacy_fallback": True,
            "turn_index": 2,
        },
        {
            "type": "runtime_evidence_sealed",
            "runtime_version": "vnext",
            "no_legacy_fallback": True,
            "mission_id": mission_id,
            "run_id": run_id,
            "journal_head_sha256": "a" * 64,
            "journal_event_count": 14,
            "report_sha256": "b" * 64,
            "report_status": "complete",
            "missing_evidence_count": 0,
            "verification_failure_count": 0,
        },
        {
            "type": "vnext_canary_done",
            "run_id": run_id,
            "mission_id": mission_id,
            "runtime_version": "vnext",
            "runtime_budget": dict(harness.FROZEN_RUNTIME_BUDGET),
            "no_legacy_fallback": True,
            "status": "mission_verified",
            "target_binding_id": "binding-1",
            "turns_used": 3,
            "model_calls": 4,
        },
    ]
    trace = tmp_path / "vnext.jsonl"
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    result = _client_result_for_trace(trace, run_id)
    audit = harness._trace_audit(
        result,
        runtime_version="vnext",
        expected_model="model-one",
        target_identity=target_identity,
        family="click_double",
    )
    assert audit["passed"] is True, audit

    events[1]["target"]["process_id"] += 1
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    rejected_target = harness._trace_audit(
        result,
        runtime_version="vnext",
        expected_model="model-one",
        target_identity=target_identity,
        family="click_double",
    )
    assert rejected_target["passed"] is False
    assert any(
        "frozen target identity mismatch" in error
        for error in rejected_target["errors"]
    )
    assert rejected_target["safety_abort_reasons"] == [
        "identity_admission_bypass"
    ]
    events[1]["target"]["process_id"] -= 1

    events.append({"type": "agent_start", "run_id": run_id, "task": harness.CANARY_TASK})
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    rejected = harness._trace_audit(
        result,
        runtime_version="vnext",
        expected_model="model-one",
        target_identity=target_identity,
        family="click_double",
    )
    assert rejected["passed"] is False
    assert any("legacy/task-stage/fallback" in error for error in rejected["errors"])
    assert "runtime_legacy_fallback" in rejected[
        "safety_abort_reasons"
    ]


def test_trace_audit_rejects_vnext_events_in_legacy_trace(tmp_path, monkeypatch):
    harness = _canary_harness()
    run_id = "legacy-child"
    forensic_root = tmp_path / "forensic"
    monkeypatch.setattr(harness.client_harness, "TRACE_ROOT", forensic_root)
    run_dir = forensic_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "001_llm_request.json").write_text(
        json.dumps({
            "run_id": run_id,
            "model": "model-one",
            "max_attempts": 1,
        }),
        encoding="utf-8",
    )
    (run_dir / "002_llm_request_attempt.json").write_text(
        json.dumps({
            "run_id": run_id,
            "attempt": 1,
            "retry_kind": "initial_request",
        }),
        encoding="utf-8",
    )
    events = [
        {
            "type": "arp_run_start",
            "run_id": "arp-outer",
            "runtime_version": "legacy",
            "task_contract": {"task": harness.CANARY_TASK},
        },
        {
            "type": "agent_start",
            "run_id": run_id,
            "task": harness.CANARY_TASK,
            "max_steps": 30,
        },
        {"type": "agent_decision", "run_id": run_id, "model": "model-one"},
    ]
    trace = tmp_path / "legacy.jsonl"
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    result = _client_result_for_trace(trace, run_id)
    result["result"]["runtime"]["current_run"] = {
        "run_id": run_id,
        "runtime_version": "legacy",
        "no_legacy_fallback": False,
    }
    audit = harness._trace_audit(
        result,
        runtime_version="legacy",
        expected_model="model-one",
        target_identity=_target_identity(55),
        family="click_double",
    )
    assert audit["passed"] is True, audit

    events.append({"type": "vnext_model_call", "run_id": run_id, "model": "model-one"})
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    rejected = harness._trace_audit(
        result,
        runtime_version="legacy",
        expected_model="model-one",
        target_identity=_target_identity(55),
        family="click_double",
    )
    assert rejected["passed"] is False
    assert any("legacy trace contains vNext" in error for error in rejected["errors"])

    events.pop()
    (run_dir / "002_llm_request_attempt.json").write_text(
        json.dumps({
            "run_id": run_id,
            "attempt": 2,
            "retry_kind": "request_retry",
        }),
        encoding="utf-8",
    )
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    rejected_retry = harness._trace_audit(
        result,
        runtime_version="legacy",
        expected_model="model-one",
        target_identity=_target_identity(55),
        family="click_double",
    )
    assert rejected_retry["passed"] is False
    assert any(
        "provider attempt forensic contains retry" in error
        for error in rejected_retry["errors"]
    )


def test_target_is_separate_and_contains_no_forbidden_gesture_surface():
    target = (
        WORKSPACE
        / "python"
        / "winpilot_home"
        / "eval_targets"
        / "vnext_canary_target.html"
    ).read_text(encoding="utf-8")
    lowered = target.casefold()
    assert "click_double" in target
    assert "scroll_roundtrip" in target
    assert "type_hotkey" in target
    assert "event.isTrusted" in target
    assert "event_seq" in target
    assert "client_wall_time_ms" in target
    assert "client_monotonic_ms" in target
    assert "screen_x" in target
    assert "screen_y" in target
    assert "visible_state" in target
    assert "state_before" in target
    assert "state_after" in target
    for forbidden in ("setpointercapture", "drag", "held pointer", "roi", "mcp"):
        assert forbidden not in lowered
