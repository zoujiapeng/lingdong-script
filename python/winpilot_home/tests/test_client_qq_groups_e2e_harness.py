from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_harness():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "client_qq_groups_e2e_run.py"
    )
    spec = importlib.util.spec_from_file_location(
        "client_qq_groups_e2e_run_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _node(
    name,
    control_type,
    rect,
    path,
    *,
    selected=None,
    scrollable=None,
    scroll_percent=None,
):
    return {
        "name": name,
        "control_type": control_type,
        "automation_id": "",
        "rect": rect,
        "path": path,
        "parent_path": "",
        "depth": 1,
        "selected": selected,
        "vertically_scrollable": scrollable,
        "vertical_scroll_percent": scroll_percent,
    }


def _qq_state(group_names, active, *, scroll_percent=0.0):
    nodes = [
        _node("群聊", "ButtonControl", [20, 80, 90, 120], "0.0"),
        _node(
            "",
            "ListControl",
            [100, 90, 430, 760],
            "0.1",
            scrollable=True,
            scroll_percent=scroll_percent,
        ),
    ]
    for index, name in enumerate(group_names):
        nodes.append(
            _node(
                name,
                "ListItemControl",
                [120, 130 + index * 64, 410, 185 + index * 64],
                f"0.1.{index}",
                selected=name == active,
            )
        )
    nodes.append(
        _node(
            active,
            "TextControl",
            [500, 70, 800, 115],
            "0.2",
        )
    )
    return nodes


def test_client_receives_only_the_original_natural_language_goal():
    harness = _load_harness()

    assert harness.TASK == (
        "去QQ看看所有的群最近都有什么消息，你汇总给我看他们在聊什么"
    )
    spec = harness.client_case_spec(timeout=600, max_steps=30)

    assert spec == {
        "task": harness.TASK,
        "max_steps": 30,
        "timeout": 600,
        "runtime_version": "vnext",
        "expected": "terminal",
    }
    assert "qq_hwnd" not in spec
    assert "oracle" not in spec
    assert "群列表" not in spec["task"]


def test_visible_state_finds_group_list_rows_and_active_group():
    harness = _load_harness()
    state = harness.analyze_visible_qq_state(
        _qq_state(["摄影群", "跑步群"], "摄影群"),
        (0, 0, 1000, 800),
    )

    assert state["group_page_seen"] is True
    assert state["group_names"] == {"摄影群", "跑步群"}
    assert state["active_group_names"] == {"摄影群"}
    assert state["list_vertically_scrollable"] is True
    assert state["list_vertical_scroll_percent"] == 0.0


def test_browsing_requires_multiple_groups_switch_scroll_and_list_end():
    harness = _load_harness()
    evidence = harness.BrowsingEvidence(b"secret", min_groups=2)
    rect = (0, 0, 1000, 800)

    first = harness.analyze_visible_qq_state(
        _qq_state(["摄影群", "跑步群"], "摄影群", scroll_percent=0.0),
        rect,
    )
    second = harness.analyze_visible_qq_state(
        _qq_state(["跑步群", "读书群"], "跑步群", scroll_percent=100.0),
        rect,
    )
    evidence.observe(
        first,
    )
    verdict = evidence.observe(
        second,
    )

    assert verdict["switch_count"] == 1
    assert verdict["scroll_transition_count"] == 1
    assert verdict["list_exhausted"] is True
    assert verdict["browse_complete"] is False
    assert verdict["coverage_complete"] is False
    assert "摄影群" not in json.dumps(verdict, ensure_ascii=False)
    assert "跑步群" not in json.dumps(verdict, ensure_ascii=False)

    third = harness.analyze_visible_qq_state(
        _qq_state(["跑步群", "读书群"], "读书群", scroll_percent=100.0),
        rect,
    )
    verdict = evidence.observe(
        third,
    )

    assert verdict["coverage_complete"] is True
    assert verdict["browse_complete"] is True
    assert verdict["discovered_group_count"] == 3
    assert verdict["visited_group_count"] == 3


def test_client_delivery_requires_nonempty_summary_for_every_group():
    harness = _load_harness()
    secret = b"delivery"

    passed = harness.evaluate_client_delivery(
        [
            "运行任务",
            "摄影群：最近在讨论周末外拍地点和器材安排。",
            "跑步群：大家在约周六晨跑并确认集合时间。",
        ],
        ["摄影群", "跑步群"],
        secret=secret,
    )
    missing = harness.evaluate_client_delivery(
        ["摄影群：最近在讨论周末外拍地点。"],
        ["摄影群", "跑步群"],
        secret=secret,
    )

    assert passed["passed"] is True
    assert passed["substantive_group_count"] == 2
    assert missing["passed"] is False
    serialized = json.dumps(passed, ensure_ascii=False)
    assert "周末外拍" not in serialized
    assert "晨跑" not in serialized


def test_trace_privacy_audit_fails_closed_on_claim_or_private_value(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "trace-1.jsonl"
    trace.write_text(
        json.dumps(
            {
                "type": "live_agent_turn",
                "concise_claim": "摄影群在讨论私人事项",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(harness.client_harness, "TRACE_ROOT", tmp_path)

    audit = harness.trace_privacy_audit(
        ["trace-1"],
        private_needles=["摄影群"],
    )

    assert audit["passed"] is False
    assert audit["unsafe_text_field_names"] == ["concise_claim"]
    assert audit["private_value_match_count"] == 1
    assert "私人事项" not in json.dumps(audit, ensure_ascii=False)


def test_current_architecture_audit_accepts_verified_delivery_projection():
    harness = _load_harness()

    audit = harness.audit_fact_delivery_projection()

    assert audit["passed"] is True
    assert audit["blocker_present"] is False
    assert audit["status"] == "delivery_projection_available"
    assert not all(audit["checks"].values())


def test_harness_wires_handoff_probe_and_redacts_base_stdout(
    monkeypatch,
    tmp_path,
    capsys,
):
    harness = _load_harness()
    calls = {}

    class Oracle:
        def __init__(self, *args, **kwargs):
            self.hwnd = 4321

        def probe(self, context):
            return {"passed": False, "run_id": context["run_id"]}

        def final_verdict(self, _result):
            return {
                "source": harness.ORACLE_SOURCE,
                "passed": False,
                "status": "blocked",
                "blockers": ["vnext_claim_not_projected_to_client"],
            }

    monkeypatch.setattr(harness, "QqWindowOracle", Oracle)
    monkeypatch.setattr(
        harness,
        "_focus_window",
        lambda hwnd: {
            "focused": True,
            "hwnd": hwnd,
            "process_id": 88,
            "executable": "QQ.exe",
            "window_rect": [0, 0, 100, 100],
        },
    )
    monkeypatch.setattr(
        harness,
        "_hold_target_until_admitted",
        lambda *args, **kwargs: {"admitted_run_id": "arp-qq"},
    )

    def fake_run_case(name, spec, **kwargs):
        calls.update({"name": name, "spec": spec, **kwargs})
        driver = object()
        handoff = kwargs["after_submit"](
            driver,
            {
                "submission_before": {"job_ids": []},
                "runtime_before": {"current_run": None},
            },
        )
        assert handoff["admitted_run_id"] == "arp-qq"
        print("PRIVATE GROUP MESSAGE MUST NOT ESCAPE")
        result = {
            "name": name,
            "expected": spec["expected"],
            "entrypoint": "electron_client_windows_uia",
            "configuration": {},
            "duration_seconds": 1.0,
            "result": {
                "outcome": "terminal",
                "status": "done",
                "run_id": "arp-qq",
                "visible_text": ["private body"],
            },
            "client": {"hwnd": 999},
            "bound_trace_ids": [],
            "execution_valid": True,
            "passed": False,
            "task_verdict": kwargs["independent_verifier"]({}),
            "report": str(tmp_path / "private-report.json"),
        }
        return result

    monkeypatch.setattr(harness.client_harness, "run_case", fake_run_case)

    report = harness.run_qq_groups_case(
        qq_hwnd=4321,
        output_root=tmp_path,
    )

    captured = capsys.readouterr()
    assert "PRIVATE GROUP MESSAGE" not in captured.out
    assert report["passed"] is False
    assert report["client_result"]["run"]["run_id"] == "arp-qq"
    assert "visible_text" not in json.dumps(report, ensure_ascii=False)
    assert calls["spec"]["task"] == harness.TASK
    assert calls["after_submit"] is not None
    assert calls["independent_completion_probe"] is not None
    assert calls["independent_verifier"] is not None
    shareable = json.loads((tmp_path / report["report"]).read_text(encoding="utf-8"))
    assert str(tmp_path) not in json.dumps(shareable, ensure_ascii=False)
    assert shareable["private_artifacts"] == {
        "directory": "private",
        "classification": "local_private_do_not_publish",
        "base_client_report": "present_under_private_directory",
    }
