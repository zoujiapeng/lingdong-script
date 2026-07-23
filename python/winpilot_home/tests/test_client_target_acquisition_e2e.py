from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import client_target_acquisition_e2e as oracle  # noqa: E402
import client_target_acquisition_fixture as fixture_module  # noqa: E402
import client_target_acquisition_runner as runner  # noqa: E402
from winpilot.process_identity import process_identity_for  # noqa: E402


RUN_ID = "arp-acquisition-1"


def _row(executable: Path, *, pid: int = 701) -> dict[str, Any]:
    created = 1_700_000_000_123_456_768
    identity = fixture_module.process_identity_for(
        process_id=pid, executable_path=str(executable),
        creation_time_ns=created,
    )
    return {
        "exe_path": str(executable), "exe_sha256": "a" * 64,
        "process_id": pid, "create_time_ns": created,
        "process_identity": identity,
        "windows": [{
            "hwnd": 801, "root_hwnd": 801, "region": [10, 20, 720, 480],
            "is_minimized": False,
        }],
    }


def _binding(row: dict[str, Any]) -> dict[str, Any]:
    window = row["windows"][0]
    binding = {
        "type": "vnext_target_binding_accepted",
        "format": "vnext-target-binding-accepted/1",
        "runtime_version": "vnext", "run_id": RUN_ID,
        "event_id": "event-bound", "mission_id": "mission-1",
        "mission_revision": 3, "obligation_id": "obligation-1",
        "binding_id": "target-binding-1",
        "hwnd": window["hwnd"], "root_hwnd": window["root_hwnd"],
        "process_id": row["process_id"],
        "process_identity": row["process_identity"],
        "region": window["region"], "epoch": 0, "frame_id": 2,
        "acquisition_action_id": "decision-acquire",
        "evidence_refs": ["evidence-binding"],
        "evidence_trust": [{
            "evidence_id": "evidence-binding",
            "kind": "authoritative_target_binding", "trust": "deterministic",
        }],
    }
    binding["canonical_identity_sha256"] = (
        oracle._binding_identity_sha256(binding)
    )
    return binding


def _trace(tmp_path: Path, row: dict[str, Any], mode: str) -> Path:
    if mode not in {"existing", "cold"}:
        raise ValueError("unsupported acquisition mode")
    queue_action = "activate_target"
    events = [
        {
            "type": "desktop_runtime_selected", "run_id": RUN_ID,
            "runtime_version": "vnext",
        },
        {
            "type": "vnext_mission_runtime_started", "run_id": RUN_ID,
            "runtime_version": "vnext", "initial_target_mode": "targetless",
            "initial_target_reason": "absent",
        },
        {
            "type": "vnext_mission_runtime_turn", "run_id": RUN_ID,
            "runtime_version": "vnext", "proposal_kind": "discover_target",
            "accepted_tool_name": None, "accepted_events": [],
        },
        {
            "type": "desktop_action_queue", "action": queue_action,
            "job_id": "queue-1", "state": "start",
        },
        {
            "type": "desktop_action_queue", "action": queue_action,
            "job_id": "queue-1", "state": "done",
        },
        {
            "type": "vnext_mission_runtime_turn", "run_id": RUN_ID,
            "runtime_version": "vnext", "proposal_kind": "desktop_action",
            "accepted_tool_name": "activate_target",
            "decision_id": "decision-acquire",
            "accepted_events": [{
                "event_id": "event-bound", "kind": "target_bound",
            }],
        },
        _binding(row),
        {
            "type": "vnext_mission_runtime_finished", "run_id": RUN_ID,
            "runtime_version": "vnext", "mission_status": "verified",
        },
        {"type": "arp_run_done", "run_id": RUN_ID, "status": "done"},
    ]
    path = tmp_path / f"{mode}.jsonl"
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in events),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("mode", "action"), [
        ("existing", "activate_target"), ("cold", "activate_target"),
    ],
)
def test_trace_contract_is_targetless_mode_specific_and_terminal(
    tmp_path, mode, action,
):
    row = _row(tmp_path / "fixture.exe")
    evidence = oracle.acquisition_trace_evidence(
        _trace(tmp_path, row, mode), expected_run_id=RUN_ID, mode=mode,
    )

    assert evidence["passed"] is True
    assert evidence["initial_target_mode"] == "targetless"
    assert evidence["queue_job_ids"] == ["queue-1"]
    assert evidence["binding"]["acquisition_action_id"] == "decision-acquire"
    assert action in json.loads(
        _trace(tmp_path, row, mode).read_text().splitlines()[3],
    )["action"]


@pytest.mark.parametrize("mutation", [
    "bound_initial", "wrong_action", "duplicate_queue", "untrusted_binding",
    "missing_terminal", "wrong_run", "duplicate_runtime_selection",
    "wrong_decision", "binding_on_wrong_turn",
    "missing_causal_ids", "duplicate_runtime_terminal", "terminal_order",
    "premature_runtime_terminal",
])
def test_trace_contract_fails_closed_on_incomplete_or_forged_evidence(
    tmp_path, mutation,
):
    row = _row(tmp_path / "fixture.exe")
    path = _trace(tmp_path, row, "existing")
    events = [
        json.loads(line) for line in path.read_text().splitlines()
    ]
    if mutation == "bound_initial":
        events[1]["initial_target_mode"] = "bound"
    elif mutation == "wrong_action":
        events[3]["action"] = events[4]["action"] = "open_app"
    elif mutation == "duplicate_queue":
        events.insert(5, copy.deepcopy(events[4]))
    elif mutation == "untrusted_binding":
        events[6]["evidence_trust"][0]["trust"] = "model_reported"
    elif mutation == "missing_terminal":
        events.pop(7)
    elif mutation == "duplicate_runtime_selection":
        events.insert(1, copy.deepcopy(events[0]))
    elif mutation == "wrong_decision":
        events[5]["decision_id"] = "decision-other"
    elif mutation == "binding_on_wrong_turn":
        events[2]["accepted_events"] = events[5]["accepted_events"]
        events[5]["accepted_events"] = []
    elif mutation == "missing_causal_ids":
        events[5].pop("decision_id")
        events[5]["accepted_events"][0].pop("event_id")
        events[6].pop("acquisition_action_id")
        events[6].pop("event_id")
    elif mutation == "duplicate_runtime_terminal":
        contradictory = copy.deepcopy(events[7])
        contradictory["mission_status"] = "failed"
        events.insert(8, contradictory)
    elif mutation == "terminal_order":
        events[7], events[8] = events[8], events[7]
    elif mutation == "premature_runtime_terminal":
        terminal = events.pop(7)
        events.insert(5, terminal)
    else:
        events[1]["run_id"] = "other-run"
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in events),
        encoding="utf-8",
    )

    result = oracle.acquisition_trace_evidence(
        path, expected_run_id=RUN_ID, mode="existing",
    )
    assert result["available"] is True
    assert result["passed"] is False
    assert result["errors"]


class _Fixture:
    def __init__(self, executable: Path, rows, records):
        self.executable = executable
        self._rows, self._records = rows, records

    def instances(self):
        return copy.deepcopy(self._rows)

    def launch_records(self):
        return copy.deepcopy(self._records)

    records_match = staticmethod(
        fixture_module.AcquisitionFixture.records_match
    )


def _client_result(trace: Path, *, mode: str) -> dict[str, Any]:
    fixture_hwnds = [801] if mode == "existing" else []
    queue = {
        "submitted": 0, "completed": 0, "failed": 0,
        "async_submitted": 0, "async_completed": 0, "async_failed": 0,
    }
    return {
        "client": {"pid": 902, "hwnd": 901},
        "runtime_before": {"desktop_action_queue": dict(queue)},
        "configuration": {
            "runtime_version": "vnext", "cold_start_toggle": 1,
            "start_paused_toggle": 1,
        },
        "session_reset": {"after": 0},
        "memory_isolation": {"passed": True},
        "result": {"run_id": RUN_ID},
        "before_submit_handoff": {
            "guard_started": True, "guard_armed": True, "shell_hwnd": 7,
            "control_surface_hwnd": 901, "control_surface_pid": 902,
            "guarded_fixture_hwnds": fixture_hwnds,
        },
        "after_submit_handoff": {
            "pause_barrier": {
                "run_id": RUN_ID, "status": "paused",
                "manual_paused": True, "started_at": None,
                "history_count": 0, "desktop_action_queue": dict(queue),
            },
            "focus_strategy": "shell_foreground_guard",
            "shell_hwnd": 7, "foreground_hwnd": 7,
            "shell_observation_count": 4,
            "verified_fixture_hwnds": fixture_hwnds,
            "resume_barrier": {
                "run_id": RUN_ID, "status": "running",
                "manual_paused": False, "control_method": "click",
                "control_surface_iconic": True, "foreground_hwnd": 7,
            },
        },
        "trace_binding": {
            "trace_file": str(trace), "passed": True,
            "run_id": RUN_ID, "parse_errors": [],
        },
    }


@pytest.mark.parametrize("mode", ["existing", "cold"])
def test_oracle_binds_trace_to_exact_process_window_and_launch(mode, tmp_path):
    executable = tmp_path / "fixture.exe"
    row = _row(executable)
    record = {
        "nonce": "public", "process_id": row["process_id"], "utc_ticks": 9,
        "exe_path": row["exe_path"], "exe_sha256": row["exe_sha256"],
    }
    before_rows = [copy.deepcopy(row)] if mode == "existing" else []
    before_records = [record] if mode == "existing" else []
    fixture = _Fixture(executable, [row], [record])
    trace = _trace(tmp_path, row, mode)

    verdict = oracle.target_acquisition_verdict(
        mode=mode, fixture=fixture,
        baseline={"instances": before_rows, "launch_records": before_records},
        client_result=_client_result(trace, mode=mode),
    )
    assert verdict["oracle_status"] == "conclusive"
    assert verdict["passed"] is True

    fixture._rows[0]["process_identity"] = process_identity_for(
        process_id=row["process_id"], executable_path=str(executable),
        creation_time_ns=row["create_time_ns"] + 1,
    )
    forged = oracle.target_acquisition_verdict(
        mode=mode, fixture=fixture,
        baseline={"instances": before_rows, "launch_records": before_records},
        client_result=_client_result(trace, mode=mode),
    )
    assert forged["oracle_status"] == "conclusive"
    assert forged["passed"] is False


def test_oracle_rejects_region_and_cold_launch_hash_drift(tmp_path):
    executable = tmp_path / "fixture.exe"
    row = _row(executable)
    record = {
        "nonce": "public", "process_id": row["process_id"], "utc_ticks": 9,
        "exe_path": row["exe_path"], "exe_sha256": row["exe_sha256"],
    }
    trace = _trace(tmp_path, row, "cold")
    fixture = _Fixture(executable, [copy.deepcopy(row)], [record])
    fixture._rows[0]["windows"][0]["region"][0] += 1
    fixture._records[0]["exe_sha256"] = "b" * 64

    verdict = oracle.target_acquisition_verdict(
        mode="cold", fixture=fixture,
        baseline={"instances": [], "launch_records": []},
        client_result=_client_result(trace, mode="cold"),
    )
    assert verdict["oracle_status"] == "conclusive"
    assert verdict["passed"] is False
    assert {
        "cold launch log path/hash/PID identity disagrees",
        "trace and fixture strong target identity disagree",
    }.issubset(verdict["errors"])


def test_missing_bound_trace_is_oracle_unavailable(tmp_path):
    executable = tmp_path / "fixture.exe"
    row = _row(executable)
    fixture = _Fixture(executable, [row], [])
    client = _client_result(tmp_path / "missing.jsonl", mode="existing")

    verdict = oracle.target_acquisition_verdict(
        mode="existing", fixture=fixture,
        baseline={"instances": [row], "launch_records": []},
        client_result=client,
    )
    assert verdict["oracle_status"] == "unavailable"
    assert verdict["status"] == "source_unavailable"
    assert verdict["passed"] is False


def test_invalid_bound_trace_is_harness_invalid_not_conclusive_failure(tmp_path):
    executable = tmp_path / "fixture.exe"
    row = _row(executable)
    fixture = _Fixture(executable, [row], [])
    path = _trace(tmp_path, row, "existing")
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events[6]["evidence_trust"][0]["trust"] = "model_reported"
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in events),
        encoding="utf-8",
    )

    verdict = oracle.target_acquisition_verdict(
        mode="existing", fixture=fixture,
        baseline={"instances": [row], "launch_records": []},
        client_result=_client_result(path, mode="existing"),
    )
    assert verdict["oracle_status"] == "unavailable"
    assert verdict["status"] == "harness_invalid"
    assert verdict["passed"] is False


@pytest.mark.parametrize("mutation", [
    "pause_run", "resume_run", "verified_hwnd", "both_hwnds", "client_hwnd",
])
def test_target_acquisition_verdict_rejects_handoff_identity_drift(
    tmp_path, mutation,
):
    executable = tmp_path / "fixture.exe"
    row = _row(executable)
    fixture = _Fixture(executable, [row], [])
    trace = _trace(tmp_path, row, "existing")
    client = _client_result(trace, mode="existing")
    if mutation == "pause_run":
        client["after_submit_handoff"]["pause_barrier"]["run_id"] = "arp-other"
    elif mutation == "resume_run":
        client["after_submit_handoff"]["resume_barrier"]["run_id"] = "arp-other"
    elif mutation == "verified_hwnd":
        client["after_submit_handoff"]["verified_fixture_hwnds"] = [802]
    elif mutation == "both_hwnds":
        client["before_submit_handoff"]["guarded_fixture_hwnds"] = [802]
        client["after_submit_handoff"]["verified_fixture_hwnds"] = [802]
    else:
        client["before_submit_handoff"]["control_surface_hwnd"] = 903

    verdict = oracle.target_acquisition_verdict(
        mode="existing", fixture=fixture,
        baseline={"instances": [row], "launch_records": []},
        client_result=client,
    )

    assert verdict["oracle_status"] == "unavailable"
    assert verdict["status"] == "harness_invalid"
    assert "targetless client handoff proof is incomplete" in verdict["errors"]


@pytest.mark.parametrize("mutation", [
    "pause_status", "pause_manual", "pause_history", "queue_changed",
    "resume_manual", "resume_foreground", "missing_start_paused",
])
def test_target_acquisition_verdict_rejects_forged_pause_or_resume_proof(
    tmp_path, mutation,
):
    executable = tmp_path / "fixture.exe"
    row = _row(executable)
    record = {
        "nonce": "public", "process_id": row["process_id"], "utc_ticks": 9,
        "exe_path": row["exe_path"], "exe_sha256": row["exe_sha256"],
    }
    client = _client_result(_trace(tmp_path, row, "existing"), mode="existing")
    if mutation == "pause_status":
        client["after_submit_handoff"]["pause_barrier"]["status"] = "running"
    elif mutation == "pause_manual":
        client["after_submit_handoff"]["pause_barrier"]["manual_paused"] = "true"
    elif mutation == "pause_history":
        client["after_submit_handoff"]["pause_barrier"]["history_count"] = 1
    elif mutation == "queue_changed":
        client["after_submit_handoff"]["pause_barrier"][
            "desktop_action_queue"
        ]["submitted"] = 1
    elif mutation == "resume_manual":
        client["after_submit_handoff"]["resume_barrier"]["manual_paused"] = True
    elif mutation == "resume_foreground":
        client["after_submit_handoff"]["resume_barrier"]["foreground_hwnd"] = 901
    else:
        client["configuration"]["start_paused_toggle"] = 0

    verdict = oracle.target_acquisition_verdict(
        mode="existing",
        fixture=_Fixture(executable, [row], [record]),
        baseline={"instances": [row], "launch_records": [record]},
        client_result=client,
    )

    assert verdict["oracle_status"] == "unavailable"
    assert verdict["status"] == "harness_invalid"
    assert verdict["passed"] is False


def test_fixture_cleanup_is_exact_and_rejects_unsafe_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fixture_module.tempfile, "gettempdir", lambda: str(tmp_path),
    )
    root = tmp_path / "winpilot-target-acquisition-safe"
    root.mkdir()
    executable, launch_log = root / "Public.exe", root / "launch.log"
    executable.write_bytes(b"fixture")
    launch_log.write_text("")
    fixture = fixture_module.AcquisitionFixture(
        root, executable, launch_log, "Public", "nonce",
        f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
        registry_created=True,
    )
    joined = []

    class GuardThread:
        @staticmethod
        def join(timeout):
            joined.append(timeout)

        @staticmethod
        def is_alive():
            return False

    guard = fixture_module._ShellForegroundGuard(901, 7)
    guard.thread = GuardThread()
    fixture._handoff_guard = guard
    monkeypatch.setattr(fixture_module, "exact_instances", lambda _path: [])
    deleted = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(fixture_module.winreg, "OpenKey", lambda *_args: Key())
    monkeypatch.setattr(
        fixture_module.winreg, "QueryValueEx",
        lambda _key, _name: (str(executable), fixture_module.winreg.REG_SZ),
    )
    monkeypatch.setattr(
        fixture_module.winreg, "DeleteKey",
        lambda root_key, key: deleted.append((root_key, key)),
    )
    assert fixture.cleanup()["passed"] is True
    assert guard.stop_event.is_set()
    assert joined == [2.0]
    assert not root.exists()
    assert deleted == [(
        fixture_module.winreg.HKEY_CURRENT_USER,
        f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
    )]

    unsafe = tmp_path / "other"
    unsafe.mkdir()
    bad = fixture_module.AcquisitionFixture(
        unsafe, unsafe / "Public.exe", unsafe / "launch.log",
        "Public", "nonce", f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
    )
    assert bad.cleanup() == {
        "passed": False, "errors": ["fixture scope is unsafe"],
    }
    assert unsafe.exists()


def test_register_failure_still_cleans_exact_partial_registry_key(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        fixture_module.tempfile, "gettempdir", lambda: str(tmp_path),
    )
    root = tmp_path / "winpilot-target-acquisition-partial"
    root.mkdir()
    executable, launch_log = root / "Public.exe", root / "launch.log"
    executable.write_bytes(b"fixture")
    launch_log.write_text("")
    fixture = fixture_module.AcquisitionFixture(
        root, executable, launch_log, "Public", "nonce",
        f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
    )
    registry_exists = False
    deleted = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_key(*_args):
        if not registry_exists:
            raise FileNotFoundError
        return Key()

    def create_key(*_args):
        nonlocal registry_exists
        registry_exists = True
        return Key()

    monkeypatch.setattr(fixture_module.winreg, "OpenKey", open_key)
    monkeypatch.setattr(fixture_module.winreg, "CreateKeyEx", create_key)
    monkeypatch.setattr(
        fixture_module.winreg, "SetValueEx",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("write failed")),
    )
    monkeypatch.setattr(
        fixture_module.winreg, "QueryValueEx",
        lambda *_args: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        fixture_module.winreg, "DeleteKey",
        lambda _root, key: deleted.append(key),
    )
    monkeypatch.setattr(fixture_module, "exact_instances", lambda _path: [])

    with pytest.raises(RuntimeError, match="write failed"):
        fixture._register()

    assert fixture.registry_created is True
    assert fixture.cleanup()["passed"] is True
    assert deleted == [fixture.registry_key]
    assert not root.exists()


def test_targetless_staging_guards_shell_without_hiding_exact_fixture(
    tmp_path, monkeypatch,
):
    executable = tmp_path / "fixture.exe"
    row = _row(executable)
    fixture = fixture_module.AcquisitionFixture(
        tmp_path, executable, tmp_path / "launch.log", "Public", "nonce",
        f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
    )
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture, "instances",
        lambda _self: copy.deepcopy([row]),
    )
    records = [{
        "nonce": "nonce", "process_id": row["process_id"], "utc_ticks": 9,
        "exe_path": row["exe_path"], "exe_sha256": row["exe_sha256"],
    }]
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture, "launch_records",
        lambda _self: copy.deepcopy(records),
    )
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture, "_verified_expected_windows",
        lambda _self, rows: [
            int(window["hwnd"])
            for item in rows for window in item["windows"]
        ],
    )

    focused = []
    monkeypatch.setattr(
        fixture_module.win32gui, "IsWindowVisible",
        lambda hwnd: hwnd == 801,
    )
    monkeypatch.setattr(
        fixture_module.win32gui, "IsIconic", lambda hwnd: hwnd == 901,
    )
    monkeypatch.setattr(
        fixture_module.win32gui, "IsWindow", lambda hwnd: hwnd in {7, 901},
    )
    monkeypatch.setattr(
        fixture_module.win32gui, "GetAncestor", lambda hwnd, _kind: hwnd,
    )
    monkeypatch.setattr(
        fixture_module.win32process, "GetWindowThreadProcessId",
        lambda hwnd: (1, 902 if hwnd == 901 else row["process_id"]),
    )
    monkeypatch.setattr(
        fixture_module.win32gui, "SetForegroundWindow",
        lambda hwnd: focused.append(hwnd),
    )
    monkeypatch.setattr(
        fixture_module.ctypes.windll.user32, "GetShellWindow", lambda: 7,
    )
    monkeypatch.setattr(
        fixture_module.ctypes.windll.user32, "GetForegroundWindow",
        lambda: 7,
    )

    staged = fixture.stage_targetless_handoff(
        [row], records, control_surface_hwnd=901, control_surface_pid=902,
    )
    deadline = fixture_module.time.monotonic() + 1.0
    while not focused and fixture_module.time.monotonic() < deadline:
        fixture_module.time.sleep(0.01)
    finished = fixture.finish_targetless_handoff([row], records)

    assert focused and set(focused) == {7}
    assert staged == {
        "shell_hwnd": 7,
        "guarded_fixture_hwnds": [801],
        "control_surface_hwnd": 901, "control_surface_pid": 902,
        "guard_armed": True,
        "guard_started": True,
    }
    assert finished["verified_fixture_hwnds"] == [801]
    assert finished["foreground_hwnd"] == 7
    assert finished["shell_observation_count"] >= 1
    assert finished["focus_strategy"] == "shell_foreground_guard"


def test_targetless_staging_rejects_identity_drift_before_any_mutation(
    tmp_path, monkeypatch,
):
    executable = tmp_path / "fixture.exe"
    baseline = _row(executable)
    drifted = copy.deepcopy(baseline)
    drifted["process_identity"] = "process-drifted"
    fixture = fixture_module.AcquisitionFixture(
        tmp_path, executable, tmp_path / "launch.log", "Public", "nonce",
        f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
    )
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture, "instances",
        lambda _self: copy.deepcopy([drifted]),
    )
    mutations = []
    monkeypatch.setattr(
        fixture_module.win32gui, "SetForegroundWindow",
        lambda *_args: mutations.append("focus"),
    )

    with pytest.raises(
        fixture_module.FixtureUnavailable,
        match="identity changed before targetless handoff",
    ):
        fixture.stage_targetless_handoff(
            [baseline], [], control_surface_hwnd=901, control_surface_pid=902,
        )

    assert mutations == []


def test_targetless_staging_rechecks_process_and_window_before_guarding(
    tmp_path, monkeypatch,
):
    executable = tmp_path / "fixture.exe"
    baseline = _row(executable)
    fixture = fixture_module.AcquisitionFixture(
        tmp_path, executable, tmp_path / "launch.log", "Public", "nonce",
        f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
    )
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture, "instances",
        lambda _self: copy.deepcopy([baseline]),
    )
    records = [{
        "nonce": "nonce", "process_id": baseline["process_id"],
        "utc_ticks": 9, "exe_path": baseline["exe_path"],
        "exe_sha256": baseline["exe_sha256"],
    }]
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture, "launch_records",
        lambda _self: copy.deepcopy(records),
    )

    class ReusedProcess:
        pid = baseline["process_id"]

        @staticmethod
        def exe():
            return str(tmp_path / "different.exe")

        @staticmethod
        def create_time():
            return baseline["create_time_ns"] / 1_000_000_000

    monkeypatch.setattr(
        fixture_module.psutil, "Process", lambda _pid: ReusedProcess(),
    )
    mutations = []
    monkeypatch.setattr(
        fixture_module.win32gui, "SetForegroundWindow",
        lambda *_args: mutations.append("focus"),
    )

    with pytest.raises(
        fixture_module.FixtureUnavailable,
        match="fixture process identity changed",
    ):
        fixture.stage_targetless_handoff(
            [baseline], records,
            control_surface_hwnd=901, control_surface_pid=902,
        )

    assert mutations == []


def test_handoff_release_rejects_an_extra_fixture_before_stopping_guard(
    tmp_path, monkeypatch,
):
    executable = tmp_path / "fixture.exe"
    baseline = _row(executable)
    extra = _row(executable, pid=702)
    record = {
        "nonce": "nonce", "process_id": baseline["process_id"],
        "utc_ticks": 9, "exe_path": baseline["exe_path"],
        "exe_sha256": baseline["exe_sha256"],
    }
    fixture = fixture_module.AcquisitionFixture(
        tmp_path, executable, tmp_path / "launch.log", "Public", "nonce",
        f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
    )
    stopped: list[str] = []

    class Guard:
        control_surface_hwnd = 901
        control_surface_pid = 902

        @staticmethod
        def stop():
            stopped.append("stop")
            return {}

    fixture._handoff_guard = Guard()
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture,
        "_verify_control_surface",
        staticmethod(lambda *_args: None),
    )
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture,
        "instances",
        lambda _self: copy.deepcopy([baseline, extra]),
    )
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture,
        "launch_records",
        lambda _self: [copy.deepcopy(record)],
    )

    with pytest.raises(
        fixture_module.FixtureUnavailable,
        match="fixture changed before handoff release",
    ):
        fixture.finish_targetless_handoff([baseline], [record])

    assert stopped == []
    assert fixture._handoff_guard is not None


def test_targetless_shell_guard_rejects_foreground_loss(monkeypatch):
    guard = fixture_module._ShellForegroundGuard(901, 7)
    guard.shell_observation_count = 1
    monkeypatch.setattr(
        fixture_module.ctypes.windll.user32, "GetForegroundWindow",
        lambda: 9,
    )

    with pytest.raises(
        fixture_module.FixtureUnavailable,
        match="shell lost foreground",
    ):
        guard.stop()


def test_targetless_shell_guard_fails_if_control_never_minimizes(monkeypatch):
    guard = fixture_module._ShellForegroundGuard(901, 7)
    monkeypatch.setattr(fixture_module.win32gui, "IsIconic", lambda _hwnd: False)

    guard.start()
    with pytest.raises(
        fixture_module.FixtureUnavailable,
        match="observed no shell foreground",
    ):
        guard.stop()


def test_targetless_shell_guard_surfaces_foreground_api_failure(monkeypatch):
    guard = fixture_module._ShellForegroundGuard(901, 7)
    monkeypatch.setattr(fixture_module.win32gui, "IsIconic", lambda _hwnd: True)
    monkeypatch.setattr(
        fixture_module.win32gui,
        "SetForegroundWindow",
        lambda _hwnd: (_ for _ in ()).throw(RuntimeError("denied")),
    )

    with pytest.raises(
        fixture_module.FixtureUnavailable,
        match="shell guard failed: RuntimeError",
    ):
        guard.start()


def test_cleanup_stops_guard_before_scope_check_and_retains_live_guard(
    tmp_path, monkeypatch,
):
    fixture = fixture_module.AcquisitionFixture(
        tmp_path, tmp_path / "fixture.exe", tmp_path / "launch.log",
        "Public", "nonce", f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
    )
    joins: list[float] = []

    class LiveThread:
        @staticmethod
        def join(timeout):
            joins.append(timeout)

        @staticmethod
        def is_alive():
            return True

    guard = fixture_module._ShellForegroundGuard(901, 7)
    guard.thread = LiveThread()
    fixture._handoff_guard = guard
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture, "_safe_scope", lambda _self: False,
    )
    monkeypatch.setattr(
        fixture_module,
        "exact_instances",
        lambda _path: pytest.fail("destructive cleanup must not start"),
    )

    result = fixture.cleanup()

    assert result == {"passed": False, "errors": ["handoff_guard:still_running"]}
    assert guard.stop_event.is_set()
    assert joins == [2.0]
    assert fixture._handoff_guard is guard


def test_cleanup_stops_guard_even_when_destructive_scope_is_unsafe(
    tmp_path, monkeypatch,
):
    fixture = fixture_module.AcquisitionFixture(
        tmp_path, tmp_path / "fixture.exe", tmp_path / "launch.log",
        "Public", "nonce", f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
    )

    class StoppedThread:
        @staticmethod
        def join(_timeout):
            return None

        @staticmethod
        def is_alive():
            return False

    guard = fixture_module._ShellForegroundGuard(901, 7)
    guard.thread = StoppedThread()
    fixture._handoff_guard = guard
    monkeypatch.setattr(
        fixture_module.AcquisitionFixture, "_safe_scope", lambda _self: False,
    )

    result = fixture.cleanup()

    assert result == {"passed": False, "errors": ["fixture scope is unsafe"]}
    assert guard.stop_event.is_set()
    assert fixture._handoff_guard is None


def test_control_surface_identity_rejects_pid_or_root_drift(monkeypatch):
    monkeypatch.setattr(fixture_module.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(fixture_module.win32gui, "GetAncestor", lambda _h, _k: 901)
    monkeypatch.setattr(
        fixture_module.win32process,
        "GetWindowThreadProcessId",
        lambda _hwnd: (1, 902),
    )

    with pytest.raises(
        fixture_module.FixtureUnavailable,
        match="control surface identity changed",
    ):
        fixture_module.AcquisitionFixture._verify_control_surface(901, 903)

    monkeypatch.setattr(fixture_module.win32gui, "GetAncestor", lambda _h, _k: 999)
    with pytest.raises(
        fixture_module.FixtureUnavailable,
        match="control surface identity changed",
    ):
        fixture_module.AcquisitionFixture._verify_control_surface(901, 902)


def test_cleanup_refuses_pid_reuse_before_any_window_or_process_mutation(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        fixture_module.tempfile, "gettempdir", lambda: str(tmp_path),
    )
    root = tmp_path / "winpilot-target-acquisition-reuse"
    root.mkdir()
    executable, launch_log = root / "Public.exe", root / "launch.log"
    executable.write_bytes(b"fixture")
    launch_log.write_text("")
    fixture = fixture_module.AcquisitionFixture(
        root, executable, launch_log, "Public", "nonce",
        f"{fixture_module.APP_PATHS_ROOT}\\Public.exe",
    )
    row = _row(executable)
    monkeypatch.setattr(
        fixture_module, "exact_instances", lambda _path: [row],
    )
    mutations = []

    class ReusedProcess:
        pid = row["process_id"]

        @staticmethod
        def exe():
            return str(root / "different.exe")

        @staticmethod
        def create_time():
            return row["create_time_ns"] / 1_000_000_000

        def terminate(self):
            mutations.append("terminate")

        def kill(self):
            mutations.append("kill")

    monkeypatch.setattr(
        fixture_module.psutil, "Process", lambda _pid: ReusedProcess(),
    )
    monkeypatch.setattr(
        fixture_module.win32gui, "PostMessage",
        lambda *_args: mutations.append("wm_close"),
    )
    result = fixture.cleanup()
    assert result["passed"] is False
    assert "process:identity_changed" in result["errors"]
    assert mutations == []


def test_process_identity_formula_matches_product_contract(tmp_path):
    path = str((tmp_path / "Fixture.exe").resolve())
    ours = fixture_module.process_identity_for(
        process_id=91, executable_path=path, creation_time_ns=123_000_000,
    )
    assert ours == process_identity_for(
        process_id=91, executable_path=path, creation_time_ns=123_000_000,
    )


def test_runner_uses_only_visible_client_entry_and_public_random_name(
    tmp_path, monkeypatch,
):
    private = str(tmp_path / "private-fixture.exe")

    class FakeFixture:
        public_name = "WinPilotAcquisition-public-nonce"
        executable = Path(private)

        def prepare(self, mode):
            assert mode == "cold"
            return {"instances": [], "launch_records": []}

        def stage_targetless_handoff(
            self, rows, records, *, control_surface_hwnd, control_surface_pid,
        ):
            assert rows == []
            assert records == []
            assert control_surface_hwnd == 901
            assert control_surface_pid == 902
            return {
                "shell_hwnd": 1, "guarded_fixture_hwnds": [],
                "control_surface_hwnd": 901, "control_surface_pid": 902,
                "guard_started": True, "guard_armed": True,
            }

        def finish_targetless_handoff(self, rows, records):
            assert rows == []
            assert records == []
            return {
                "verified_fixture_hwnds": [], "foreground_hwnd": 1,
                "shell_observation_count": 4,
                "focus_strategy": "shell_foreground_guard",
            }

        def cleanup(self):
            return {"passed": True, "errors": []}

    captured = {}

    class Harness:
        @staticmethod
        def run_case(name, spec, **kwargs):
            captured.update({"name": name, "spec": spec, **kwargs})
            class Driver:
                identity = type("Identity", (), {"hwnd": 901, "pid": 902})()

                @staticmethod
                def capture_desktop(name):
                    return str(tmp_path / name)

                @staticmethod
                def runtime_status():
                    return {
                        "running": True,
                        "desktop_action_queue": {
                            "submitted": 0, "completed": 0, "failed": 0,
                            "async_submitted": 0, "async_completed": 0,
                            "async_failed": 0,
                        },
                        "current_run": {
                            "run_id": "arp-new", "status": "paused",
                            "manual_paused": True,
                            "goal": spec["task"],
                        },
                    }

                @staticmethod
                def run_detail(run_id):
                    assert run_id == "arp-new"
                    return {
                        "run": {
                            "run_id": run_id, "status": "paused",
                            "manual_paused": True,
                        },
                        "arp": {
                            "run_id": run_id, "status": "paused",
                            "manual_paused": True, "started_at": None,
                            "history": [],
                        },
                    }

                @staticmethod
                def resume_paused_run(run_id):
                    return {
                        "run_id": run_id, "status": "running",
                        "manual_paused": False, "control_method": "click",
                        "control_surface_iconic": True, "foreground_hwnd": 1,
                    }

            partial = {
                "runtime_before": {
                    "current_run": {"run_id": "arp-old"},
                },
            }
            captured["before_submit"] = kwargs["before_submit"](
                Driver(), partial,
            )
            captured["handoff"] = kwargs["after_submit"](Driver(), partial)
            return {
                "passed": True,
                "task_verdict": {"passed": True},
                "runtime_before": {"model_profile": {"model": "before"}},
                "before_submit_handoff": captured["before_submit"],
            }

    monkeypatch.setattr(runner, "_interactive_desktop", lambda: True)
    monkeypatch.setattr(
        runner.AcquisitionFixture, "create", lambda _seed: FakeFixture(),
    )
    result = runner.run_target_acquisition_case(
        "target-cold-launch",
        {
            "seed": 4, "mode": "cold",
            "task_template": "打开应用 {public_name}",
            "max_steps": 12, "timeout": 30,
        },
        output_root=tmp_path, group_id=None, pair_id=None,
        client_harness=Harness,
        evidence_projector=lambda client_result: {
            "projected_client_result": client_result["passed"],
        },
    )

    assert result["passed"] is True
    assert captured["reset_session"] is True
    assert captured["submit_without_focus"] is True
    assert captured["spec"]["runtime_version"] == "vnext"
    assert captured["spec"]["start_paused"] is True
    assert captured["spec"]["task"] == (
        "打开应用 WinPilotAcquisition-public-nonce"
    )
    assert private not in json.dumps(captured, default=str)
    assert result["projected_client_result"] is True
    assert result["targetless_precondition"]["guard_started"] is True
    assert captured["before_submit"]["desktop_screenshot"].endswith(
        "targetless-before-submit.png",
    )
    assert captured["handoff"]["focus_strategy"] == (
        "shell_foreground_guard"
    )
    assert captured["handoff"]["pause_barrier"]["run_id"] == "arp-new"
    assert captured["handoff"]["pause_barrier"]["status"] == "paused"
    assert captured["handoff"]["resume_barrier"]["run_id"] == "arp-new"
    assert captured["handoff"]["verified_fixture_hwnds"] == []
    assert captured["handoff"]["desktop_screenshot"].endswith(
        "targetless-before-resume.png",
    )


def test_handoff_barrier_rejects_new_run_that_is_already_terminal():
    class Driver:
        @staticmethod
        def runtime_status():
            return {
                "running": False,
                "current_run": {
                    "run_id": "arp-new", "status": "done", "goal": "task",
                },
            }

        @staticmethod
        def run_detail(run_id):
            return {
                "run": {"run_id": run_id, "status": "done"},
                "arp": {"run_id": run_id, "status": "done"},
            }

    with pytest.raises(
        runner.FixtureUnavailable,
        match="did not enter the atomic pause barrier",
    ):
        runner._wait_for_submitted_run(
            Driver(),
            {"runtime_before": {"current_run": {"run_id": "arp-old"}}},
            task="task", timeout=0.1,
        )


def test_handoff_barrier_waits_for_canonical_pause_surfaces_to_converge():
    detail_reads = 0

    class Driver:
        @staticmethod
        def runtime_status():
            return {
                "running": True,
                "current_run": {
                    "run_id": "arp-new", "status": "paused",
                    "manual_paused": True, "goal": "task",
                },
            }

        @staticmethod
        def run_detail(run_id):
            nonlocal detail_reads
            detail_reads += 1
            projection_ready = detail_reads > 1
            return {
                "run": {
                    "run_id": run_id,
                    "status": "paused" if projection_ready else "queued",
                    "manual_paused": projection_ready,
                },
                "arp": {
                    "run_id": run_id, "status": "paused",
                    "manual_paused": True, "started_at": None,
                    "history": [],
                },
            }

    barrier = runner._wait_for_submitted_run(
        Driver(),
        {"runtime_before": {"current_run": {"run_id": "arp-old"}}},
        task="task", timeout=0.5,
    )

    assert detail_reads == 2
    assert barrier["run_id"] == "arp-new"
    assert barrier["history_count"] == 0


def test_handoff_barrier_never_waits_after_execution_started():
    class Driver:
        @staticmethod
        def runtime_status():
            return {
                "running": True,
                "current_run": {
                    "run_id": "arp-new", "status": "paused",
                    "manual_paused": True, "goal": "task",
                },
            }

        @staticmethod
        def run_detail(run_id):
            return {
                "run": {
                    "run_id": run_id, "status": "paused",
                    "manual_paused": True,
                },
                "arp": {
                    "run_id": run_id, "status": "paused",
                    "manual_paused": True, "started_at": 1.0,
                    "history": [],
                },
            }

    with pytest.raises(
        runner.FixtureUnavailable,
        match="crossed the atomic pause barrier",
    ):
        runner._wait_for_submitted_run(
            Driver(),
            {"runtime_before": {"current_run": {"run_id": "arp-old"}}},
            task="task", timeout=0.5,
        )


def test_runner_stops_foreground_guard_before_client_error_cleanup(
    tmp_path, monkeypatch,
):
    calls: list[str] = []

    class FakeFixture:
        public_name = "PublicFixture"

        def prepare(self, _mode):
            return {"instances": [], "launch_records": []}

        def stage_targetless_handoff(self, *_args, **_kwargs):
            calls.append("guard_started")
            return {
                "guard_started": True, "guard_armed": True,
                "shell_hwnd": 7, "control_surface_hwnd": 901,
                "control_surface_pid": 902, "guarded_fixture_hwnds": [],
            }

        def abort_targetless_handoff(self):
            calls.append("guard_stopped")

        def cleanup(self):
            calls.append("fixture_cleanup")
            return {"passed": True, "errors": []}

    fake = FakeFixture()

    class Harness:
        @staticmethod
        def run_case(_name, spec, **kwargs):
            class Driver:
                identity = type("Identity", (), {"hwnd": 901, "pid": 902})()

                @staticmethod
                def capture_desktop(name):
                    return str(tmp_path / name)

                @staticmethod
                def runtime_status():
                    return {
                        "running": True,
                        "current_run": {
                            "run_id": "arp-paused", "status": "paused",
                            "goal": spec["task"],
                        },
                    }

                @staticmethod
                def run_detail(run_id):
                    return {
                        "run": {"run_id": run_id, "status": "done"},
                        "arp": {"run_id": run_id, "status": "done"},
                    }

            partial = {"runtime_before": {"current_run": None}}
            kwargs["before_submit"](Driver(), partial)
            with pytest.raises(
                runner.FixtureUnavailable,
                match="atomic pause barrier",
            ):
                kwargs["after_submit"](Driver(), partial)
            cleanup = kwargs["before_error_cleanup"](Driver(), partial)
            assert cleanup == {"targetless_guard_aborted": True}
            calls.append("client_error_cleanup")
            assert calls.index("guard_stopped") < calls.index("client_error_cleanup")
            return {
                "passed": False,
                "task_verdict": {"source": "external", "passed": False},
                "before_submit_handoff": {},
            }

    monkeypatch.setattr(runner, "_interactive_desktop", lambda: True)
    monkeypatch.setattr(runner.AcquisitionFixture, "create", lambda _seed: fake)
    result = runner.run_target_acquisition_case(
        "target-cold-launch",
        {
            "seed": 1, "mode": "cold", "task_template": "打开 {public_name}",
            "max_steps": 5, "timeout": 5,
        },
        output_root=tmp_path, group_id=None, pair_id=None,
        client_harness=Harness, evidence_projector=lambda _value: {},
    )

    assert result["passed"] is False
    assert calls == [
        "guard_started", "guard_stopped", "client_error_cleanup",
        "fixture_cleanup",
    ]


def test_runner_marks_missing_compiler_or_interactive_environment_unavailable(
    tmp_path, monkeypatch,
):
    class Harness:
        @staticmethod
        def run_case(*_args, **_kwargs):
            raise AssertionError("client must not run without fixture")

    monkeypatch.setattr(runner, "_interactive_desktop", lambda: True)
    monkeypatch.setattr(
        runner.AcquisitionFixture, "create",
        lambda _seed: (_ for _ in ()).throw(
            runner.FixtureUnavailable("csc.exe is unavailable"),
        ),
    )
    result = runner.run_target_acquisition_case(
        "target-cold-launch",
        {
            "seed": 4, "mode": "cold",
            "task_template": "打开应用 {public_name}",
            "max_steps": 12, "timeout": 30,
        },
        output_root=tmp_path, group_id=None, pair_id=None,
        client_harness=Harness,
        evidence_projector=lambda _client_result: {},
    )
    assert result["passed"] is False
    assert result["external_verdict"]["oracle_status"] == "unavailable"
    assert "FixtureUnavailable" in result["runner_error"]
