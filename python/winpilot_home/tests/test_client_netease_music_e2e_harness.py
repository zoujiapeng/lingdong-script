from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path


def _load_harness():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "client_netease_music_e2e_run.py"
    )
    spec = importlib.util.spec_from_file_location(
        "client_netease_music_e2e_run_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _node(
    name: str,
    control_type: str,
    rect: list[int],
    path: str,
    *,
    parent_path: str = "",
    automation_id: str = "",
):
    return {
        "name": name,
        "control_type": control_type,
        "rect": rect,
        "path": path,
        "parent_path": parent_path,
        "automation_id": automation_id,
    }


def _successful_nodes():
    return [
        _node(
            "",
            "ListItemControl",
            [100, 180, 900, 225],
            "0.2",
        ),
        _node(
            "宁夏",
            "TextControl",
            [150, 185, 260, 215],
            "0.2.0",
            parent_path="0.2",
        ),
        _node(
            "梁静茹",
            "TextControl",
            [350, 185, 450, 215],
            "0.2.1",
            parent_path="0.2",
        ),
        _node(
            "宁夏",
            "TextControl",
            [140, 720, 260, 750],
            "0.8.0",
            parent_path="0.8",
        ),
        _node(
            "梁静茹",
            "TextControl",
            [270, 720, 360, 750],
            "0.8.1",
            parent_path="0.8",
        ),
        _node(
            "暂停",
            "ButtonControl",
            [470, 710, 530, 770],
            "0.8.2",
            parent_path="0.8",
        ),
    ]


def test_client_gets_only_original_natural_language_goal():
    harness = _load_harness()

    assert harness.TASK == (
        "去网易云音乐搜索梁静茹并播放对应列表的第一首歌，保持音量百分之5"
    )
    spec = harness.client_case_spec(timeout=600, max_steps=40)

    assert spec == {
        "task": harness.TASK,
        "max_steps": 40,
        "timeout": 600,
        "runtime_version": "vnext",
        "expected": "independent_success",
    }
    assert "netease_hwnd" not in spec
    assert "target_volume" not in spec
    assert "expected_track" not in spec


def test_readonly_coreaudio_reader_uses_only_getters():
    harness = _load_harness()

    class Endpoint:
        def GetMasterVolumeLevelScalar(self):
            return 0.05

        def GetMute(self):
            return 0

    result = harness.read_coreaudio_master_volume(lambda: Endpoint())

    assert result == {
        "source": "windows_coreaudio_iaudioendpointvolume_readback",
        "level_scalar": 0.05,
        "level_percent": 5.0,
        "muted": False,
    }
    source = inspect.getsource(harness.read_coreaudio_master_volume)
    assert ".SetMasterVolume" not in source
    assert ".SetMute" not in source
    assert "volume_set" not in source


def test_visible_playback_first_result_and_five_percent_pass_candidate():
    harness = _load_harness()
    verdict = harness.evaluate_music_state(
        _successful_nodes(),
        {
            "source": "coreaudio",
            "level_percent": 5.0,
            "muted": False,
        },
        (0, 0, 1000, 800),
    )

    assert verdict["candidate_passed"] is True
    assert verdict["current_track_candidates"] == ["宁夏"]
    assert verdict["first_result_track_candidates"] == ["宁夏"]
    assert verdict["first_result_match"] is True
    assert verdict["volume_matches"] is True


def test_pause_artist_and_track_must_be_in_bottom_player_region():
    harness = _load_harness()
    nodes = _successful_nodes()
    for node in nodes[3:]:
        node["rect"] = [
            node["rect"][0],
            300,
            node["rect"][2],
            340,
        ]

    verdict = harness.evaluate_music_state(
        nodes,
        {"level_percent": 5.0, "muted": False},
        (0, 0, 1000, 800),
    )

    assert verdict["candidate_passed"] is False
    assert "player region does not identify Liang Jingru" in verdict["errors"]
    assert (
        "player transport does not expose a playing/pause state"
        in verdict["errors"]
    )


def test_generic_play_pause_label_is_not_proof_of_active_playback():
    harness = _load_harness()
    nodes = _successful_nodes()
    nodes[-1]["name"] = "播放/暂停"

    verdict = harness.evaluate_music_state(
        nodes,
        {"level_percent": 5.0, "muted": False},
        (0, 0, 1000, 800),
    )

    assert verdict["candidate_passed"] is False
    assert not verdict["playing_pause_nodes"]


def test_muted_or_wrong_volume_fails_even_when_player_is_correct():
    harness = _load_harness()

    muted = harness.evaluate_music_state(
        _successful_nodes(),
        {"level_percent": 5.0, "muted": True},
        (0, 0, 1000, 800),
    )
    wrong = harness.evaluate_music_state(
        _successful_nodes(),
        {"level_percent": 8.0, "muted": False},
        (0, 0, 1000, 800),
    )

    assert muted["candidate_passed"] is False
    assert wrong["candidate_passed"] is False
    assert muted["volume_matches"] is False
    assert wrong["volume_matches"] is False


def test_second_result_cannot_pass_as_the_first_song():
    harness = _load_harness()
    nodes = _successful_nodes()
    nodes[3]["name"] = "勇气"

    verdict = harness.evaluate_music_state(
        nodes,
        {"level_percent": 5.0, "muted": False},
        (0, 0, 1000, 800),
    )

    assert verdict["candidate_passed"] is False
    assert verdict["first_result_match"] is False
    assert "current track does not match the first visible result" in verdict[
        "errors"
    ]


def test_missing_result_row_fails_closed_instead_of_trusting_agent_claim():
    harness = _load_harness()
    nodes = _successful_nodes()[3:]

    verdict = harness.evaluate_music_state(
        nodes,
        {"level_percent": 5.0, "muted": False},
        (0, 0, 1000, 800),
    )

    assert verdict["candidate_passed"] is False
    assert "first visible search-result title is not observable" in verdict[
        "errors"
    ]


def test_admission_binding_accepts_only_a_new_vnext_arp_job():
    harness = _load_harness()
    run_id, job_id = harness._new_admitted_run(
        {
            "runtime_jobs": [
                {
                    "job_id": "old-job",
                    "kind": "arp",
                    "metadata": {
                        "run_id": "old-run",
                        "runtime_version": "vnext",
                    },
                },
                {
                    "job_id": "new-job",
                    "kind": "arp",
                    "metadata": {
                        "run_id": "new-run",
                        "runtime_version": "vnext",
                    },
                },
            ]
        },
        previous_run_id="old-run",
        before_job_ids={"old-job"},
    )

    assert (run_id, job_id) == ("new-run", "new-job")


def test_current_run_fallback_must_match_the_submitted_goal():
    harness = _load_harness()
    run_id, job_id = harness._new_admitted_run(
        {
            "runtime_jobs": [],
            "current_run": {
                "run_id": "unrelated-run",
                "runtime_version": "vnext",
                "goal": "an unrelated goal",
            },
        },
        previous_run_id="old-run",
        before_job_ids=set(),
        expected_goal=harness.TASK,
    )

    assert (run_id, job_id) == ("", "")


def test_harness_wires_visible_handoff_and_independent_probe(monkeypatch, tmp_path):
    harness = _load_harness()
    calls = {}

    class Oracle:
        hwnd = 4321
        identity = {"hwnd": 4321}

        def __init__(self, *args, **kwargs):
            pass

        def capture_baseline(self):
            return {"passed": False}

        def probe(self, context):
            return {"passed": True, "run_id": context["run_id"]}

        def final_verdict(self):
            return {"source": harness.ORACLE_SOURCE, "passed": True}

    monkeypatch.setattr(harness, "NetEaseWindowOracle", Oracle)
    monkeypatch.setattr(
        harness,
        "_focus_window",
        lambda hwnd: {"focused": True, "hwnd": hwnd},
    )
    monkeypatch.setattr(
        harness,
        "_start_target_handoff_watcher",
        lambda **kwargs: {
            "stop": type("Stop", (), {"set": lambda self: None})(),
            "thread": type(
                "Thread",
                (),
                {"join": lambda self, timeout: None, "is_alive": lambda self: False},
            )(),
            "evidence": {},
            "deadline": 9999999999.0,
        },
    )
    monkeypatch.setattr(
        harness,
        "_finish_target_handoff_watcher",
        lambda watcher: {"started": bool(watcher)},
    )
    monkeypatch.setattr(
        harness,
        "_hold_target_until_admitted",
        lambda *args, **kwargs: {"admitted_run_id": "arp-music"},
    )

    def fake_run_case(name, spec, **kwargs):
        calls.update({"name": name, "spec": spec, **kwargs})
        driver = type(
            "Driver",
            (),
            {"identity": type("Identity", (), {"hwnd": 999})()},
        )()
        client_result = {
            "submission_before": {"job_ids": []},
            "runtime_before": {"current_run": None},
        }
        kwargs["before_submit"](driver, client_result)
        handoff = kwargs["after_submit"](driver, client_result)
        assert handoff["admitted_run_id"] == "arp-music"
        assert kwargs["independent_completion_probe"](
            {"run_id": "arp-music"}
        )["passed"]
        assert kwargs["independent_verifier"]({})["passed"]
        return {
            "passed": True,
            "bound_trace_ids": [],
        }

    monkeypatch.setattr(harness.client_harness, "run_case", fake_run_case)

    result = harness.run_netease_music_case(
        netease_hwnd=4321,
        output_root=tmp_path,
    )

    assert result["passed"] is True
    assert calls["spec"]["task"] == harness.TASK
    assert calls["reset_session"] is True
    assert calls["before_submit"] is not None
    assert calls["after_submit"] is not None
    assert calls["independent_completion_probe"] is not None
    assert calls["independent_verifier"] is not None
