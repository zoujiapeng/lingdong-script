from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw


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


def _ocr_element(text, cx, cy):
    return SimpleNamespace(text=text, cx=cx, cy=cy)


def _fake_ocr(rows):
    def fn(image):
        del image
        return [_ocr_element(text, cx, cy) for text, cx, cy in rows]

    return fn


def _qq_window_image(*, selected_y=None):
    """One 1920x1029 QQ-like window: white rows, one light-blue active row."""
    image = Image.new("RGB", (1920, 1029), (255, 255, 255))
    if selected_y is not None:
        draw = ImageDraw.Draw(image)
        draw.rectangle(
            [40, selected_y - 8, 420, selected_y + 8],
            fill=(217, 233, 252),
        )
    return image


def _ocr_qq_state(group_names, active, *, selected_y=None):
    rows = []
    for index, name in enumerate(group_names):
        cy = 143 + index * 79
        rows.append((name, 187, cy))
        rows.append((f"{name}-preview", 192, cy + 25))
        rows.append(("17-52", 268, cy))
    state = harness_ocr_analysis(_qq_window_image(selected_y=selected_y), rows)
    if active:
        state["active_group_names"] = {active}
    return state


def harness_ocr_analysis(image, rows):
    return _load_harness().analyze_visible_qq_state_from_image(
        image,
        ocr_fn=_fake_ocr(rows),
    )


def test_ocr_state_detects_rows_and_active_group_from_pixels():
    harness = _load_harness()
    image = _qq_window_image(selected_y=222)
    rows = [
        ("Operit", 187, 143),
        ("何晨光海", 192, 168),
        ("17-52", 268, 143),
        ("我的手机", 180, 222),
        ("分析整个项目", 209, 247),
        ("16-27", 268, 222),
        ("轨迹轮鼠", 184, 302),
        ("alpakka轨迹轮鼠", 213, 327),
    ]

    state = harness.analyze_visible_qq_state_from_image(
        image,
        ocr_fn=_fake_ocr(rows),
    )

    assert state["source"] == "ocr"
    assert state["group_page_seen"] is True
    assert state["group_names"] == {"Operit", "我的手机", "轨迹轮鼠"}
    assert state["active_group_names"] == {"我的手机"}
    assert state["list_vertical_scroll_percent"] is None
    assert state["list_vertically_scrollable"] is None
    assert state["ocr_row_count"] == 3


def test_ocr_rows_filter_times_counts_and_preview_lines():
    harness = _load_harness()
    image = _qq_window_image()
    rows = [
        ("23届摄影群", 180, 143),
        ("+99", 268, 143),
        ("07/29", 268, 168),
        ("1417", 268, 222),
        ("17-52", 268, 302),
        ("C++群", 184, 382),
        ("Astra API", 190, 461),
    ]

    state = harness.analyze_visible_qq_state_from_image(
        image,
        ocr_fn=_fake_ocr(rows),
    )

    assert state["group_names"] == {"23届摄影群", "C++群", "Astra API"}
    assert state["group_page_seen"] is True
    assert state["active_group_names"] == set()


def test_ocr_mode_browse_complete_needs_switches_not_scroll_or_end():
    harness = _load_harness()
    evidence = harness.BrowsingEvidence(b"secret", min_groups=2)
    first = _ocr_qq_state(["摄影群", "跑步群"], "摄影群", selected_y=143)
    second = _ocr_qq_state(["摄影群", "跑步群"], "跑步群", selected_y=222)

    evidence.observe(first, mode="ocr")
    verdict = evidence.observe(second, mode="ocr")

    assert verdict["oracle_mode"] == "ocr"
    assert verdict["switch_count"] == 1
    assert verdict["scroll_transition_count"] == 0
    assert verdict["list_exhausted"] is False
    assert verdict["coverage_complete"] is True
    assert verdict["verification_gaps"] == [
        "list_exhausted",
        "scroll_position",
    ]
    assert verdict["browse_complete"] is True
    assert "摄影群" not in json.dumps(verdict, ensure_ascii=False)
    assert "跑步群" not in json.dumps(verdict, ensure_ascii=False)


def test_ocr_mode_never_completes_without_switches():
    harness = _load_harness()
    evidence = harness.BrowsingEvidence(b"secret", min_groups=2)
    first = _ocr_qq_state(["摄影群", "跑步群"], "摄影群", selected_y=143)
    second = _ocr_qq_state(["摄影群", "跑步群"], "摄影群", selected_y=143)

    evidence.observe(first, mode="ocr")
    verdict = evidence.observe(second, mode="ocr")

    assert verdict["switch_count"] == 0
    assert verdict["browse_complete"] is False


def test_ocr_mode_requires_visiting_every_discovered_row():
    harness = _load_harness()
    evidence = harness.BrowsingEvidence(b"secret", min_groups=2)
    first = _ocr_qq_state(["摄影群", "跑步群"], "摄影群", selected_y=143)

    verdict = evidence.observe(first, mode="ocr")

    assert verdict["coverage_complete"] is False
    assert verdict["browse_complete"] is False


def test_oracle_falls_back_to_ocr_when_qq_uia_is_black_box(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    fake_identity = {
        "hwnd": 4242,
        "process_id": 2896,
        "executable": "QQ.exe",
        "executable_path": "C:/QQ.exe",
        "title": "QQ",
        "window_rect": [0, 0, 1920, 1029],
    }
    monkeypatch.setattr(
        harness,
        "_window_identity",
        lambda hwnd: dict(fake_identity, hwnd=hwnd),
    )
    monkeypatch.setattr(
        harness.win32gui,
        "GetForegroundWindow",
        lambda: 4242,
    )
    monkeypatch.setattr(harness, "_visible_texts_for_window", lambda hwnd: [])

    image = _qq_window_image(selected_y=222)
    rows = [
        ("Operit", 187, 143),
        ("我的手机", 180, 222),
        ("轨迹轮鼠", 184, 302),
    ]
    oracle = harness.QqWindowOracle(
        4242,
        tmp_path / "private",
        capture=lambda *args, **kwargs: image,
        snapshot=lambda hwnd: [
            {
                "name": "QQ",
                "control_type": "PaneControl",
                "rect": [0, 0, 1920, 1029],
            }
        ],
        ocr_fn=_fake_ocr(rows),
    )

    probe = oracle.probe({"run_id": "arp-ocr-mode"})

    assert probe["oracle_mode"] == "ocr"
    assert probe["uia_node_count"] == 1
    assert probe["browsing"]["discovered_group_count"] == 3
    assert probe["browsing"]["group_page_seen"] is True
    assert probe["status"] == "observing_visible_qq"

    verdict = oracle.final_verdict(
        {
            "client": {"hwnd": 999},
            "after_submit_handoff": {
                "target_hwnd": 4242,
                "target_process_id": 2896,
                "target_foreground_at_admission": True,
                "admitted_run_id": "arp-ocr-mode",
            },
            "result": {"run_id": "arp-ocr-mode"},
            "bound_trace_ids": [],
        }
    )

    assert verdict["oracle_mode"] == "ocr"
    assert verdict["verification_gaps"] == [
        "list_exhausted",
        "scroll_position",
    ]
    assert "group_list_scroll_not_observed" not in verdict["blockers"]
    assert "all_groups_traversal_not_proven" not in verdict["blockers"]


def test_uia_rich_mode_merges_ocr_selection_when_selection_state_missing(
    monkeypatch,
    tmp_path,
):
    """UIA gives row names but no selected state: pixels prove the active row."""
    harness = _load_harness()
    fake_identity = {
        "hwnd": 4242,
        "process_id": 2896,
        "executable": "QQ.exe",
        "executable_path": "C:/QQ.exe",
        "title": "QQ",
        "window_rect": [0, 0, 1920, 1029],
    }
    monkeypatch.setattr(
        harness,
        "_window_identity",
        lambda hwnd: dict(fake_identity, hwnd=hwnd),
    )
    monkeypatch.setattr(
        harness.win32gui,
        "GetForegroundWindow",
        lambda: 4242,
    )
    monkeypatch.setattr(harness, "_visible_texts_for_window", lambda hwnd: [])

    image = _qq_window_image(selected_y=222)
    rows = [
        ("轨迹轮鼠", 184, 302),
        ("我的手机", 180, 222),
        ("摄影群", 184, 382),
    ]
    uia_nodes = [
        {
            "name": "QQ",
            "control_type": "PaneControl",
            "rect": [0, 0, 1920, 1029],
        },
        {
            "name": "最小化",
            "control_type": "ButtonControl",
            "rect": [0, 0, 40, 40],
        },
        {
            "name": "群聊",
            "control_type": "ButtonControl",
            "rect": [20, 60, 60, 100],
        },
        {
            "name": "轨迹轮鼠手柄交流群",
            "control_type": "ButtonControl",
            "rect": [100, 290, 400, 330],
            "selected": None,
        },
        {
            "name": "我的手机",
            "control_type": "ButtonControl",
            "rect": [100, 210, 400, 250],
            "selected": None,
        },
        {
            "name": "摄影爱好者交流群",
            "control_type": "ButtonControl",
            "rect": [100, 370, 400, 410],
            "selected": None,
        },
    ]
    oracle = harness.QqWindowOracle(
        4242,
        tmp_path / "private",
        capture=lambda *args, **kwargs: image,
        snapshot=lambda hwnd: uia_nodes,
        ocr_fn=_fake_ocr(rows),
    )

    probe = oracle.probe({"run_id": "arp-uia-ocr"})

    assert probe["oracle_mode"] == "uia+ocr"
    assert probe["browsing"]["discovered_group_count"] == 3
    assert probe["browsing"]["visited_group_count"] == 1
    assert probe["browsing"]["switch_count"] == 0
    assert probe["browsing"]["verification_gaps"] == [
        "list_exhausted",
        "scroll_position",
    ]


def test_oracle_reports_unmeasurable_when_ocr_engine_is_missing(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    fake_identity = {
        "hwnd": 4242,
        "process_id": 2896,
        "executable": "QQ.exe",
        "executable_path": "C:/QQ.exe",
        "title": "QQ",
        "window_rect": [0, 0, 1920, 1029],
    }
    monkeypatch.setattr(
        harness,
        "_window_identity",
        lambda hwnd: dict(fake_identity, hwnd=hwnd),
    )
    monkeypatch.setattr(
        harness.win32gui,
        "GetForegroundWindow",
        lambda: 4242,
    )
    monkeypatch.setattr(harness, "_visible_texts_for_window", lambda hwnd: [])

    image = _qq_window_image()
    oracle = harness.QqWindowOracle(
        4242,
        tmp_path / "private",
        capture=lambda *args, **kwargs: image,
        snapshot=lambda hwnd: [
            {
                "name": "QQ",
                "control_type": "PaneControl",
                "rect": [0, 0, 1920, 1029],
            }
        ],
        ocr_fn=lambda image: None,
    )

    probe = oracle.probe({"run_id": "arp-ocr-down"})

    assert probe["oracle_mode"] == "ocr"
    assert probe["browsing"]["ocr_unavailable"] is True
    assert probe["status"] == "ocr_unavailable"

    verdict = oracle.final_verdict(
        {
            "client": {"hwnd": 999},
            "after_submit_handoff": {
                "target_hwnd": 4242,
                "target_process_id": 2896,
                "target_foreground_at_admission": True,
                "admitted_run_id": "arp-ocr-down",
            },
            "result": {"run_id": "arp-ocr-down"},
            "bound_trace_ids": [],
        }
    )

    assert verdict["passed"] is False
    assert verdict["status"] == "unmeasurable"
    assert "qq_ocr_unavailable" in verdict["blockers"]


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
