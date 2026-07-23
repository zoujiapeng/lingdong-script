from __future__ import annotations

import json
import random

import pytest

from winpilot.process_identity import (
    ProcessIdentityError,
    process_identity_for as neutral_process_identity_for,
)
from winpilot.vnext.targeting import (
    TargetActivation,
    TargetCandidate,
    TargetContractError,
    TargetDiscovery,
    TargetDiscoverySnapshot,
    TargetState,
    candidate_ref_for,
    launch_identity_for,
    process_identity_for,
)


def _process_identity(pid: int) -> str:
    return process_identity_for(
        process_id=pid,
        executable_path=fr"C:\Apps\host-{pid}.exe",
        creation_time_ns=100,
    )


def test_process_identity_is_reexported_from_neutral_visual_runtime_owner():
    assert process_identity_for is neutral_process_identity_for


def test_process_identity_reexport_rejects_relative_executable_path():
    with pytest.raises(ProcessIdentityError, match="absolute"):
        process_identity_for(
            process_id=7,
            executable_path="host.exe",
            creation_time_ns=100,
        )


def _discovery(*, installed=(), visible=(), running=None):
    running = running or {}
    return TargetDiscovery(
        installed_resolver=lambda _query, _limit: list(installed),
        visible_windows=lambda: list(visible),
        process_windows=lambda term: running.get(
            term.casefold(), {"running": False, "windows": []}
        ),
        process_identity=_process_identity,
        clock=lambda: 123.5,
    )


def test_candidate_contract_redacts_runtime_and_launch_identity_from_actor_view():
    identity = launch_identity_for(
        kind="exe", target=r"C:\Apps\Orbit\orbit.exe",
    )
    candidate = TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="open_app", identity=identity,
        ),
        activation="open_app",
        state="installed",
        label="Orbit Workspace",
        provenance=("installed_catalog",),
        launch_kind="exe",
        launch_target=r"C:\Apps\Orbit\orbit.exe",
    )

    view = candidate.actor_view()

    assert view == {
        "candidate_ref": candidate.candidate_ref,
        "activation": "open_app",
        "state": "installed",
        "label": "Orbit Workspace",
        "window_title": None,
        "provenance": ["installed_catalog"],
    }
    wire = json.dumps(view)
    assert "orbit.exe" not in wire.casefold()
    assert "hwnd" not in wire and "process_id" not in wire


def test_open_candidate_ref_cannot_be_reused_for_another_launch_target():
    identity = launch_identity_for(kind="exe", target=r"C:\Apps\safe.exe")
    with pytest.raises(TargetContractError, match="does not identify"):
        TargetCandidate(
            candidate_ref=candidate_ref_for(
                activation="open_app", identity=identity,
            ),
            activation="open_app",
            state="installed",
            label="Workspace",
            provenance=("installed_catalog",),
            launch_kind="exe",
            launch_target=r"C:\Apps\other.exe",
        )


@pytest.mark.parametrize("target", ["", "   ", '""'])
def test_launch_identity_rejects_empty_targets_after_normalization(target):
    with pytest.raises(TargetContractError):
        launch_identity_for(kind="exe", target=target)


@pytest.mark.parametrize("notices", ["one_notice", ["one_notice"], (1,)])
def test_snapshot_rejects_noncanonical_notices(notices):
    with pytest.raises(TargetContractError, match="tuple of strings"):
        TargetDiscoverySnapshot(
            query="Workspace", candidates=(), captured_at=1, notices=notices,
        )


def test_snapshot_rejects_noncanonical_notice_text():
    with pytest.raises(TargetContractError, match="canonical runtime tokens"):
        TargetDiscoverySnapshot(
            query="Workspace", candidates=(), captured_at=1,
            notices=("source unavailable: details",),
        )


def test_candidate_rejects_non_string_window_title():
    identity = _process_identity(7)
    with pytest.raises(TargetContractError, match="window_title must be a string"):
        TargetCandidate(
            candidate_ref=candidate_ref_for(
                activation="switch_window", identity=f"1|7|{identity}",
            ),
            activation="switch_window",
            state="visible",
            label="Workspace",
            provenance=("visible_windows",),
            window_title=123,
            hwnd=1,
            process_id=7,
            process_identity=identity,
        )


@pytest.mark.parametrize("bad_identity", [True, 1.9, "123"])
@pytest.mark.parametrize("field", ["hwnd", "pid"])
def test_discovery_rejects_coerced_window_identity(field, bad_identity):
    raw = {"hwnd": 123, "pid": 8, "title": "Workspace", "visible": True}
    raw[field] = bad_identity

    snapshot = _discovery(visible=(raw,)).discover("Workspace")

    assert snapshot.candidates == ()


@pytest.mark.parametrize("bad_visible", [0, 1, "false", None])
def test_discovery_rejects_non_boolean_visibility(bad_visible):
    raw = {
        "hwnd": 123,
        "pid": 8,
        "title": "Workspace",
        "visible": bad_visible,
    }

    snapshot = _discovery(visible=(raw,)).discover("Workspace")

    assert snapshot.candidates == ()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"hwnd": 0, "process_id": 2, "process_identity": "p"},
        {"hwnd": 1, "process_id": 0, "process_identity": "p"},
        {"hwnd": 1, "process_id": 2, "process_identity": ""},
    ],
)
def test_switch_candidate_requires_strong_window_process_identity(kwargs):
    with pytest.raises(TargetContractError):
        TargetCandidate(
            candidate_ref=candidate_ref_for(
                activation="switch_window", identity="invalid-window-identity",
            ),
            activation="switch_window",
            state="visible",
            label="Orbit",
            provenance=("visible_windows",),
            **kwargs,
        )


def test_candidate_ref_is_bound_to_private_physical_identity():
    with pytest.raises(TargetContractError, match="does not identify"):
        TargetCandidate(
            candidate_ref=candidate_ref_for(
                activation="open_app", identity="exe:c:/other.exe",
            ),
            activation="open_app",
            state="installed",
            label="Orbit",
            provenance=("installed_catalog",),
            launch_kind="exe",
            launch_target=r"C:\orbit.exe",
        )


def test_discovery_prefers_and_merges_existing_instance_over_launch_candidate():
    app = {
        "name": "Orbit Workspace",
        "kind": "exe",
        "target": r"C:\Apps\Orbit\orbit.exe",
        "process_name": "orbit.exe",
        "aliases": ["orbit"],
    }
    window = {
        "hwnd": 101,
        "pid": 41,
        "title": "Orbit - document",
        "visible": True,
    }
    discovery = _discovery(
        installed=(app,),
        visible=(window,),
        running={
            "orbit.exe": {"running": True, "windows": [window]},
            "orbit": {"running": True, "windows": [window]},
            "orbit workspace": {"running": True, "windows": [window]},
        },
    )

    snapshot = discovery.discover("Orbit Workspace")

    assert len(snapshot.candidates) == 1
    candidate = snapshot.candidates[0]
    assert candidate.activation is TargetActivation.SWITCH_WINDOW
    assert candidate.state is TargetState.VISIBLE
    assert candidate.hwnd == 101
    assert candidate.provenance == (
        "installed_catalog", "running_processes", "title_match", "visible_windows",
    )


def test_title_match_before_catalog_correlation_still_suppresses_duplicate_launch():
    app = {
        "name": "Orbit Workspace",
        "kind": "exe",
        "target": r"C:\Apps\Orbit\orbit.exe",
        "process_name": "orbit.exe",
    }
    window = {
        "hwnd": 102,
        "pid": 42,
        "title": "Orbit Workspace",
        "visible": True,
    }

    snapshot = _discovery(
        installed=(app,),
        visible=(window,),
        running={"orbit.exe": {"running": True, "windows": [window]}},
    ).discover("Orbit Workspace")

    assert len(snapshot.candidates) == 1
    assert snapshot.candidates[0].activation is TargetActivation.SWITCH_WINDOW


def test_switch_candidate_ref_changes_when_pid_or_process_identity_changes():
    raw = {"hwnd": 103, "pid": 43, "title": "Orbit", "visible": True}
    first = _discovery(visible=(raw,)).discover("Orbit").candidates[0]
    second = TargetDiscovery(
        installed_resolver=lambda *_args: [],
        visible_windows=lambda: [{**raw, "pid": 44}],
        process_windows=lambda _term: {},
        process_identity=_process_identity,
        clock=lambda: 123.5,
    ).discover("Orbit").candidates[0]

    assert first.candidate_ref != second.candidate_ref


def test_switch_candidate_ref_detects_process_reuse_with_same_hwnd_and_pid():
    raw = {"hwnd": 104, "pid": 45, "title": "Orbit", "visible": True}
    first = _discovery(visible=(raw,)).discover("Orbit").candidates[0]
    second = TargetDiscovery(
        installed_resolver=lambda *_args: [],
        visible_windows=lambda: [raw],
        process_windows=lambda _term: {},
        process_identity=lambda pid: process_identity_for(
            process_id=pid,
            executable_path=fr"C:\Apps\host-{pid}.exe",
            creation_time_ns=101,
        ),
        clock=lambda: 123.5,
    ).discover("Orbit").candidates[0]

    assert first.candidate_ref != second.candidate_ref


def test_switch_actor_view_omits_all_private_window_and_process_identity():
    candidate = _discovery(visible=({
        "hwnd": 105, "pid": 46, "title": "Orbit", "visible": True,
    },)).discover("Orbit").candidates[0]

    wire = json.dumps(candidate.actor_view())

    for private_field in (
        "hwnd", "process_id", "process_identity", "launch_kind", "launch_target",
    ):
        assert private_field not in wire


def test_hidden_running_window_is_recoverable_and_suppresses_duplicate_launch():
    app = {
        "name": "Nebula Chat",
        "kind": "appid",
        "target": "Vendor.Nebula!App",
        "process_name": "nebula.exe",
    }
    hidden = {
        "hwnd": 202,
        "pid": 52,
        "title": "Nebula Chat",
        "visible": False,
    }
    snapshot = _discovery(
        installed=(app,),
        running={
            "nebula.exe": {"running": True, "windows": [hidden]},
            "nebula chat": {"running": True, "windows": [hidden]},
        },
    ).discover("Nebula Chat")

    assert [(item.activation.value, item.state.value) for item in snapshot.candidates] == [
        ("switch_window", "recoverable")
    ]


def test_running_process_without_bindable_window_never_becomes_launch_candidate():
    app = {
        "name": "Nebula Chat",
        "kind": "exe",
        "target": r"C:\Apps\Nebula.exe",
        "process_name": "nebula.exe",
    }
    snapshot = _discovery(
        installed=(app,),
        running={
            "nebula.exe": {"running": True, "windows": []},
            "nebula chat": {"running": True, "windows": []},
        },
    ).discover("Nebula Chat")

    assert snapshot.candidates == ()
    assert "running_without_bindable_window" in snapshot.notices


def test_unknown_running_state_never_becomes_launch_candidate():
    discovery = TargetDiscovery(
        installed_resolver=lambda *_args: [{
            "name": "Nebula", "kind": "exe", "target": r"C:\Nebula.exe",
        }],
        visible_windows=lambda: [],
        process_windows=lambda _term: (_ for _ in ()).throw(RuntimeError("denied")),
        process_identity=_process_identity,
        clock=lambda: 1,
    )

    snapshot = discovery.discover("Nebula")

    assert snapshot.candidates == ()
    assert "running_state_unavailable" in snapshot.notices


def test_truncated_process_terms_cannot_prove_application_is_not_running():
    app = {
        "name": "Nebula",
        "kind": "exe",
        "target": r"C:\Nebula.exe",
        "aliases": [*(f"alias-{index}" for index in range(12)), "zz-running"],
    }
    discovery = TargetDiscovery(
        installed_resolver=lambda *_args: [app],
        visible_windows=lambda: [],
        process_windows=lambda term: {
            "running": term == "zz-running", "windows": [],
        },
        process_identity=_process_identity,
        clock=lambda: 1,
    )

    snapshot = discovery.discover("Nebula")

    assert snapshot.candidates == ()
    assert "running_state_unavailable" in snapshot.notices


def test_visible_observation_wins_over_recoverable_observation_for_same_window():
    app = {
        "name": "Nebula", "kind": "exe", "target": r"C:\Nebula.exe",
        "process_name": "nebula.exe",
    }
    visible = {"hwnd": 204, "pid": 54, "title": "Nebula", "visible": True}
    hidden = {**visible, "visible": False}
    snapshot = _discovery(
        installed=(app,), visible=(visible,),
        running={"nebula.exe": {"running": True, "windows": [hidden]}},
    ).discover("Nebula")

    assert len(snapshot.candidates) == 1
    assert snapshot.candidates[0].state is TargetState.VISIBLE


def test_ambiguous_installed_candidates_remain_separate_for_actor_choice():
    snapshot = _discovery(installed=(
        {"name": "Comet Canvas", "kind": "lnk", "target": r"C:\A\Comet.lnk"},
        {"name": "Comet Notes", "kind": "appid", "target": "Vendor.Comet!Notes"},
    )).discover("Comet")

    assert [item.label for item in snapshot.candidates] == [
        "Comet Canvas", "Comet Notes",
    ]
    assert len({item.candidate_ref for item in snapshot.candidates}) == 2


def test_duplicate_catalog_rows_for_same_launch_identity_are_merged():
    rows = (
        {"name": "Orbit", "kind": "exe", "target": r"C:\Apps\Orbit.exe"},
        {"name": "ORBIT renamed", "kind": "exe", "target": r"c:\apps\ORBIT.exe"},
    )

    snapshot = _discovery(installed=rows).discover("Orbit")

    assert len(snapshot.candidates) == 1
    assert snapshot.candidates[0].activation is TargetActivation.OPEN_APP


def test_unrelated_window_title_match_does_not_hide_installed_launch_candidate():
    app = {
        "name": "Orbit", "kind": "exe", "target": r"C:\Apps\Orbit.exe",
    }
    browser = {
        "hwnd": 306, "pid": 66, "title": "Orbit - Web Browser", "visible": True,
    }

    snapshot = _discovery(installed=(app,), visible=(browser,)).discover("Orbit")

    assert {item.activation for item in snapshot.candidates} == {
        TargetActivation.SWITCH_WINDOW, TargetActivation.OPEN_APP,
    }


def test_many_title_only_windows_cannot_starve_installed_launch_candidate():
    app = {
        "name": "Orbit", "kind": "exe", "target": r"C:\Apps\Orbit.exe",
    }
    windows = tuple(
        {
            "hwnd": 400 + index,
            "pid": 80 + index,
            "title": f"Orbit result {index}",
            "visible": True,
        }
        for index in range(24)
    )

    snapshot = _discovery(installed=(app,), visible=windows).discover("Orbit")

    assert len(snapshot.candidates) == 16
    assert any(
        item.activation is TargetActivation.OPEN_APP
        for item in snapshot.candidates
    )


def test_multilingual_titles_match_without_ascii_only_reduction():
    windows = (
        {"hwnd": 301, "pid": 61, "title": "Редактор заметок", "visible": True},
        {"hwnd": 302, "pid": 62, "title": "메모 편집기", "visible": True},
        {"hwnd": 303, "pid": 63, "title": "ノート編集", "visible": True},
    )

    assert _discovery(visible=windows).discover("Редактор").candidates[0].hwnd == 301
    assert _discovery(visible=windows).discover("메모").candidates[0].hwnd == 302
    assert _discovery(visible=windows).discover("ノート").candidates[0].hwnd == 303


def test_punctuation_only_reduction_does_not_create_broad_title_match():
    windows = (
        {"hwnd": 304, "pid": 64, "title": "Calculator", "visible": True},
        {"hwnd": 305, "pid": 65, "title": "C++ Workspace", "visible": True},
    )

    snapshot = _discovery(visible=windows).discover("C++")

    assert [item.hwnd for item in snapshot.candidates] == [305]


def test_candidate_identity_is_stable_across_order_label_case_and_unicode_form():
    first = {
        "name": "Ｎｅｂｕｌａ Desk",
        "kind": "exe",
        "target": r"C:\Apps\Nebula\desk.exe",
    }
    second = {
        "name": "nebula desk renamed",
        "kind": "exe",
        "target": r"c:\apps\nebula\DESK.exe",
    }

    left = _discovery(installed=(first,)).discover("Nebula").candidates[0]
    right = _discovery(installed=(second,)).discover("nebula").candidates[0]

    assert left.candidate_ref == right.candidate_ref


def test_random_source_order_does_not_change_actor_projection():
    installed = [
        {"name": "Orbit A", "kind": "exe", "target": r"C:\Apps\A.exe"},
        {"name": "Orbit B", "kind": "appid", "target": "Vendor.Orbit!B"},
        {"name": "Orbit C", "kind": "lnk", "target": r"C:\Start\C.lnk"},
    ]
    expected = _discovery(installed=installed).discover("Orbit").to_json()
    rng = random.Random(8128)
    for _ in range(50):
        shuffled = list(installed)
        rng.shuffle(shuffled)
        assert _discovery(installed=shuffled).discover("Orbit").to_json() == expected


def test_source_failure_is_explicit_and_does_not_fabricate_empty_success():
    discovery = TargetDiscovery(
        installed_resolver=lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
        visible_windows=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        process_windows=lambda _term: {},
        process_identity=_process_identity,
        clock=lambda: 7,
    )

    snapshot = discovery.discover("Orbit")

    assert snapshot.candidates == ()
    assert snapshot.notices == (
        "installed_catalog_unavailable", "visible_window_catalog_unavailable",
    )


def test_snapshot_lookup_is_fail_closed_and_not_a_replay_ledger():
    candidate = _discovery(installed=({
        "name": "Orbit", "kind": "exe", "target": r"C:\Orbit.exe",
    },)).discover("Orbit").candidates[0]
    snapshot = TargetDiscoverySnapshot(
        query="Orbit", candidates=(candidate,), captured_at=1,
    )

    assert snapshot.candidate(candidate.candidate_ref) == candidate
    with pytest.raises(TargetContractError):
        snapshot.candidate("target-000000000000000000000000")

    assert "captured_at" not in snapshot.actor_view()


@pytest.mark.parametrize("query", ["", "   ", "x" * 257])
def test_discovery_rejects_invalid_query_without_calling_sources(query):
    called = []
    discovery = TargetDiscovery(
        installed_resolver=lambda *_args: called.append("installed") or [],
        visible_windows=lambda: called.append("windows") or [],
        process_windows=lambda _term: {},
        process_identity=_process_identity,
    )

    with pytest.raises(TargetContractError):
        discovery.discover(query)
    assert called == []
