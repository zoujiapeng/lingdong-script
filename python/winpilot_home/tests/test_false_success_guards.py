import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def coordinate_semantic_snap_enabled(monkeypatch):
    """Opt into coordinate refinement only in tests that exercise it."""
    from winpilot.agent import interaction_tools

    original_get = interaction_tools.CONFIG.get

    def configured(*path, default=None):
        if path == ("controller", "coordinate_semantic_snap"):
            return True
        return original_get(*path, default=default)

    monkeypatch.setattr(interaction_tools.CONFIG, "get", configured)


@pytest.fixture
def mocked_visual_judge_capability(monkeypatch):
    """Declare an image-capable judge for tests that submit mocked pixels."""
    from winpilot.agent import done_verifier as done_mod

    class _Config:
        _data = {
            "api": {"model": "mock-vision-judge", "temperature": 0.0},
            "agent": {
                "escalation_model": "",
                "verify_done": True,
                "done_max_rejects": 2,
            },
            "vlm": {"enabled": False, "base_url": "", "model": ""},
        }

        def get(self, *path, default=None):
            node = self._data
            for key in path:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node

    monkeypatch.setattr(done_mod, "_config", lambda: _Config())
    monkeypatch.setattr(
        done_mod,
        "model_supports_vision",
        lambda model, provider="": model == "mock-vision-judge",
    )


def test_visual_done_without_declared_vision_judge_fails_closed(monkeypatch):
    from winpilot.agent import done_verifier as done_mod
    from winpilot.agent.loop import AgentRunner

    class _Config:
        _data = {
            "api": {"model": "mock-text-judge"},
            "agent": {"escalation_model": ""},
            "vlm": {"enabled": False, "base_url": "", "model": ""},
        }

        def get(self, *path, default=None):
            node = self._data
            for key in path:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node

    monkeypatch.setattr(done_mod, "_config", lambda: _Config())
    monkeypatch.setattr(
        done_mod,
        "model_supports_vision",
        lambda _model, provider="": False,
    )
    runner = AgentRunner("inspect the current visual state")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._fresh_done_image_b64 = "mock-fresh-frame"

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **_kwargs):
                    raise AssertionError("text-only judge must not receive image content")

    assert runner._call_done_judge(_Client(), "", "", "") is False
    assert "no configured vision model" in runner._last_done_reject_reason


@pytest.mark.parametrize(
    "invalid_verified",
    ["true", "false", "1", "yes", 1, -1, 0, 1.0, [], {}],
    ids=[
        "string-true",
        "string-false",
        "string-one",
        "string-yes",
        "integer-one",
        "integer-minus-one",
        "integer-zero",
        "float-one",
        "list",
        "dict",
    ],
)
def test_track_verification_requires_literal_boolean_true(invalid_verified):
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner.__new__(AgentRunner)
    runner._last_verify_ok = None
    runner._had_verified_action = False

    runner._track_verification(
        "click",
        json.dumps({"ok": True, "verified": invalid_verified}),
    )

    assert runner._last_verify_ok is False
    assert runner._had_verified_action is False


def test_track_verification_accepts_literal_boolean_true():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner.__new__(AgentRunner)
    runner._last_verify_ok = None
    runner._had_verified_action = False

    runner._track_verification(
        "click",
        json.dumps({"ok": True, "verified": True}),
    )

    assert runner._last_verify_ok is True
    assert runner._had_verified_action is True


def test_xlsx_artifact_verifier_checks_cells_formulas_results_and_bold_rows(tmp_path):
    from zipfile import ZipFile

    from winpilot.agent.done_verifier import _xlsx_task_errors, _xlsx_workbook_evidence

    path = tmp_path / "report.xlsx"
    shared = ["商品", "数量", "单价", "小计", "键盘", "鼠标", "显示器", "合计", "测试标识", "SHEET_GUI_42"]
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{value}</t></si>" for value in shared)
        + "</sst>"
    )
    styles_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2"><font/><font><b/></font></fonts>
  <cellXfs count="2"><xf fontId="0"/><xf fontId="1" applyFont="1"/></cellXfs>
</styleSheet>'''
    sheet_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
  <row r="1"><c r="A1" t="s" s="1"><v>0</v></c><c r="B1" t="s" s="1"><v>1</v></c><c r="C1" t="s" s="1"><v>2</v></c><c r="D1" t="s" s="1"><v>3</v></c></row>
  <row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2"><v>2</v></c><c r="C2"><v>199</v></c><c r="D2"><f>B2*C2</f><v>398</v></c></row>
  <row r="3"><c r="A3" t="s"><v>5</v></c><c r="B3"><v>3</v></c><c r="C3"><v>89</v></c><c r="D3"><f>B3*C3</f><v>267</v></c></row>
  <row r="4"><c r="A4" t="s"><v>6</v></c><c r="B4"><v>1</v></c><c r="C4"><v>1299</v></c><c r="D4"><f>B4*C4</f><v>1299</v></c></row>
  <row r="5"><c r="A5" t="s" s="1"><v>7</v></c><c r="D5" s="1"><f>SUM(D2:D4)</f><v>1964</v></c></row>
  <row r="7"><c r="A7" t="s"><v>8</v></c><c r="B7" t="s"><v>9</v></c></row>
</sheetData></worksheet>'''
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        archive.writestr("xl/styles.xml", styles_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    task = '''Enter this exact table into cells A1:D7:
商品\t数量\t单价\t小计
键盘\t2\t199\t=B2*C2
鼠标\t3\t89\t=B3*C3
显示器\t1\t1299\t=B4*C4
合计\t\t\t=SUM(D2:D4)
\t\t\t
测试标识\tSHEET_GUI_42\t\t
Make row 1 bold and row 5 bold. D5 evaluates to 1964.'''

    text, cells, package_error = _xlsx_workbook_evidence(path)

    assert package_error == ""
    assert "D5=1964 formula:=SUM(D2:D4)" in text
    assert _xlsx_task_errors(task, cells) == []

    cells["D5"]["value"] = "999"
    assert any("D5 cached result" in error for error in _xlsx_task_errors(task, cells))


def test_visible_state_done_accepts_fresh_screen_ocr_spacing(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("点击按钮后确认状态卡出现 LOCAL_COUNTER")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._recent_results = [("observe", "Changed-only observe found status card")]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Target"\n[1] text "LOCAL COUNTER 1"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("visible state evidence should skip LLM judge")

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True
def test_visible_state_targets_extract_historical_list():
    from winpilot.agent.task_rules import _visible_state_targets

    assert _visible_state_targets(
        "确认 APPLIED_TEXT、LOCAL_COUNTER、MODAL_TARGET_VISIBLE 都曾出现后再完成"
    ) == ["APPLIED_TEXT", "LOCAL_COUNTER", "MODAL_TARGET_VISIBLE"]


def test_visible_state_targets_ignore_descriptive_ui_nouns():
    from winpilot.agent.task_rules import _visible_state_targets

    assert _visible_state_targets(
        "Close the currently visible browser restore popup, then confirm READY_STATE is visible."
    ) == ["READY_STATE"]


def test_visible_state_targets_ignore_english_articles_before_visual_descriptions():
    from winpilot.agent.task_rules import _visible_state_targets

    assert _visible_state_targets(
        "Confirm the red line actually appears before finishing."
    ) == []

def test_done_defers_missing_visible_payload_to_fresh_native_judge(
    monkeypatch,
    mocked_visual_judge_capability,
):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("Type the requested sentence into the visible editor.")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._recent_results = [("type_text", '{"ok": true}')]

    monkeypatch.setattr(
        "winpilot.agent.done_verifier._required_visible_payloads",
        lambda _task: ["payload that must be visible"],
    )

    def fresh_native_screen():
        runner._fresh_done_image_b64 = "fresh-frame"
        return 'Window: "Editor"\n[1] document ""'

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_native_screen)
    captured = {}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "payload is legible in the editor"}'
                        ))
                    )])

    assert runner._verify_done(_Client()) is True
    assert runner._pending_visual_payloads == ["payload that must be visible"]
    user_content = captured["messages"][1]["content"]
    prompt = next(part["text"] for part in user_content if part.get("type") == "text")
    assert "AUTHORITATIVE VISIBLE-PAYLOAD CHECK" in prompt
    assert "payload that must be visible" in prompt
    assert any(part.get("type") == "image_url" for part in user_content)


def test_done_allows_present_distinctive_literals_to_reach_judge(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("Type FAST_WRAP_OK_071 into the visible input.")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._recent_results = [("observe", "APPLIED_TEXT_1: FAST_WRAP_OK_071")]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Target"\n[1] text "FAST_WRAP_OK_071"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content='{"done": true, "reason": "literal visible"}'))]
                    )

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True


def test_visible_evidence_ledger_ignores_negative_query_hits():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("confirm APPLIED_TEXT appeared")

    runner._track_visible_evidence(
        "observe",
        {},
        'No elements matched query "APPLIED_TEXT" among 70 elements',
    )

    assert runner._visible_evidence_ledger == []

    runner._track_visible_evidence(
        "observe",
        {},
        '[12] text "APPLIED_TEXT: STAGING_INPUT_731"',
    )

    assert runner._visible_evidence_ledger[-1]["target"] == "APPLIED_TEXT"


def test_visible_evidence_ignores_reducer_why_and_trace_path_metadata():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("calculate 2026*7*12")
    runner._track_visible_evidence(
        "observe",
        {},
        'top_candidates:\n- id=5 role=pane text="0" why=readable; text related to "2026"\n'
        'Perception trace: 20260712-run\\perception_001_full.json',
    )

    assert runner._visible_evidence_ledger == []


def test_subgoal_evidence_ignores_observe_query_and_browser_url():
    from winpilot.fast.subgoal import _pick_text

    fake = {
        "results": [
            {
                "action": "observe",
                "full_text": (
                    'Changed-only observe query fallback: no changed text matched query "CALIBRATION_OK"; '
                    "returned a full observe snapshot instead.\n"
                    '[4] text "http://127.0.0.1:8877/gui_agent_target.html?calibration=1" '
                    "center=(412,80) bbox=(120,64,680,28) src=uia\n"
                    "Perception reducer:\n"
                    '- text="http://127.0.0.1:8877/gui_agent_target.html?calibration=1" '
                    'why="query CALIBRATION_OK came from the request"\n'
                    "No elements matched query CALIBRATION_OK among 80 elements."
                ),
            }
        ]
    }

    visible = _pick_text(fake)

    assert "CALIBRATION_OK" not in visible
    assert "calibration=1" not in visible

    true_visible = _pick_text({
        "results": [
            {
                "action": "read_text",
                "preview": "CALIBRATION_OK screen=618,642 client=216,318 button_offset=42,18",
            }
        ]
    })
    assert "CALIBRATION_OK screen=" in true_visible


def test_visible_state_done_accepts_historical_evidence_for_appeared_task(monkeypatch):
    import queue

    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.log import BUS

    runner = AgentRunner(
        "确认 APPLIED_TEXT、LOCAL_COUNTER、MODAL_TARGET_VISIBLE 都曾出现后再完成"
    )
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._track_visible_evidence("observe", {}, "APPLIED_TEXT_1: demo")
    runner._track_visible_evidence("read_text", {}, "LOCAL_COUNTER_4")
    runner._track_visible_evidence("observe", {}, "靶场状态：MODAL_TARGET_VISIBLE")

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Target"\n[1] text "MODAL_CLOSED"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("historical visible evidence should skip LLM judge")

    events = BUS.subscribe()
    try:
        assert runner._verify_done(_Client()) is True
        seen = []
        while True:
            try:
                seen.append(events.get_nowait())
            except queue.Empty:
                break
    finally:
        BUS.unsubscribe(events)

    done_checks = [event for event in seen if event.get("type") == "done_check"]
    assert runner._done_verified is True
    assert done_checks[-1]["passed"] is True
    assert "historical visible evidence" in done_checks[-1]["reason"]


def test_historical_visible_ledger_outranks_current_native_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Confirm APPLIED_TEXT_1 and LOCAL_COUNTER_4 have appeared before finishing."
    )
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._track_visible_evidence("observe", {}, "APPLIED_TEXT_1")
    runner._track_visible_evidence("read_text", {}, "LOCAL_COUNTER_4")

    def fresh_screen():
        runner._fresh_done_image_b64 = "fresh-current-frame"
        return "The historical targets are no longer on the current page."

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_screen)

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **_kwargs):
                    raise AssertionError("historical appeared evidence should not require current visibility")

    assert runner._verify_done(_Client()) is True


def test_visible_state_done_still_requires_fresh_screen_without_appeared_word(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("确认 APPLIED_TEXT、LOCAL_COUNTER、MODAL_TARGET_VISIBLE 当前可见")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._recent_results = [
        ("observe", "APPLIED_TEXT_1: demo\nLOCAL_COUNTER_4\nMODAL_TARGET_VISIBLE"),
    ]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Target"\n[1] text "MODAL_CLOSED"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content='{"done": false, "reason": "missing fresh visible states"}'))]
                    )

    assert runner._verify_done(_Client()) is False


def test_historical_visible_done_ignores_agent_summary_without_tool_evidence(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("确认 APPLIED_TEXT、LOCAL_COUNTER、MODAL_TARGET_VISIBLE 都曾出现后再完成")
    runner.session.hwnd = 123
    runner.result_text = "我看到三个目标都曾出现"
    runner._recent_results = [
        ("done", "agent summary says APPLIED_TEXT LOCAL_COUNTER MODAL_TARGET_VISIBLE appeared"),
    ]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Target"\n[1] text "MODAL_CLOSED"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content='{"done": false, "reason": "missing historical tool evidence"}'))]
                    )

    assert runner._verify_done(_Client()) is False


def test_visible_evidence_ignores_negative_query_result():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("确认 LOCAL_COUNTER 出现过后再完成")

    runner._track_visible_evidence(
        "observe",
        {},
        'No elements matched query "local_counter" among 74 elements.',
    )

    assert runner._visible_evidence_ledger == []


def test_visible_evidence_tracks_nested_batch_read_text():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "confirm APPLIED_TEXT_1 and LOCAL_COUNTER_4 have appeared before finishing"
    )
    runner._track_visible_evidence(
        "run_action_batch",
        {},
        json.dumps({
            "ok": True,
            "results": [
                {
                    "action": "read_text",
                    "preview": "APPLIED_TEXT_1: DEMO\nLOCAL_COUNTER_4",
                    "ok": True,
                }
            ],
        }),
    )

    targets = {item["target"] for item in runner._visible_evidence_ledger}
    assert {"APPLIED_TEXT_1", "LOCAL_COUNTER_4"} <= targets


def test_observation_evidence_waits_for_explicit_done_verification(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner(
        "confirm APPLIED_TEXT_1, E2E_TARGET_101 and LOCAL_COUNTER_4 have appeared"
    )
    result = ToolResult(
        "Window: Target\nAPPLIED_TEXT_1: E2E_TARGET_101\nLOCAL_COUNTER_4",
        is_observe=True,
    )
    runner._track_visible_evidence("read_text", {}, result.text)
    assert result.terminal is None
    assert not hasattr(runner, "_maybe_auto_done_from_visible_evidence")

    runner.session.hwnd = 123
    runner.result_text = "done"
    monkeypatch.setattr(runner, "_fresh_done_screen", lambda: result.text)

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "visible evidence proves the request"}'
                        ))
                    )])

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True


def test_done_gate_requires_independent_judge_after_deterministic_saved_artifact(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        r"Save SHEET_GATE_42 with value 1964 exactly to "
        r"C:\Users\zou\Desktop\sheet-gate.xlsx"
    )
    runner.run_state.unverified_actions = 0
    monkeypatch.setattr(runner, "_done_evidence", lambda: ("", "artifact evidence"))
    monkeypatch.setattr(runner, "_fresh_done_screen", lambda: "sheet-gate.xlsx - WPS Office")
    monkeypatch.setattr(runner, "_verify_visible_payloads", lambda _screen, _evidence: True)
    monkeypatch.setattr(
        runner,
        "_deterministic_saved_file_verdict",
        lambda _screen, _evidence: (True, "saved XLSX structure verified"),
    )
    captured = {}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "artifact content and GUI state agree"}'
                        ))
                    )])

    assert runner._verify_done(client=_Client()) is True
    assert runner._done_verified is True
    prompt = captured["messages"][1]["content"]
    assert "deterministic_saved_file" in prompt


def test_failed_subgoal_packet_cannot_enter_visible_evidence():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("type STALE_TOKEN_811, click Apply, and verify it")
    result_text = json.dumps({
        "ok": False,
        "verified": False,
        "blocked": True,
        "evidence_packet": {
            "matched_texts": ["STALE_TOKEN_811"],
            "partial_matched_texts": ["STALE_TOKEN_811"],
            "missing_texts": [],
        },
    })

    runner._track_visible_evidence("execute_subgoal", {}, result_text)

    assert runner._visible_evidence_ledger == []


def test_done_gate_rejects_pending_failed_or_unverified_action():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("type STALE_TOKEN_811 and click Apply")
    runner.run_state.unverified_actions = 1
    runner.run_state.last_tool = "execute_subgoal"
    runner.run_state.last_issue = "Apply control was not found"

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("pending action must fail before the LLM judge")

    assert runner._verify_done(_Client()) is False
    assert "1 desktop action" in runner._last_done_reject_reason
    assert "Apply control was not found" in runner._last_done_reject_reason


def test_coding_done_uses_deterministic_judge_despite_gui_counter(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("write report.txt containing REPORT_READY and verify it")
    runner.run_state.unverified_actions = 3
    runner._recent_results = [
        ("run_shell", '{"ok":true,"exit_code":0,"stdout":"FILE_EXISTS=True REPORT_READY"}'),
    ]
    monkeypatch.setattr(runner, "_fresh_done_screen", lambda: "")
    monkeypatch.setattr(runner, "_call_done_judge", lambda *_args, **_kwargs: True)

    assert runner._verify_done(object()) is True
def test_visible_evidence_rejects_literal_echoed_by_agent_control_surface():
    from winpilot.agent.loop import AgentRunner

    task = (
        "Open the target page. Use a real physical mouse press, then confirm "
        "PRESS_PULSE_ACTIVE appeared during the press."
    )
    runner = AgentRunner(task)

    runner._track_visible_evidence(
        "read_text", {}, "Window: Lingdong Script\nAgent command\n" + task,
    )

    assert runner._visible_evidence_ledger == []


def test_done_does_not_treat_task_echo_as_visual_completion_evidence(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    task = (
        "Open the target page. Use a real physical mouse press, then confirm "
        "PRESS_PULSE_ACTIVE appeared during the press."
    )
    runner = AgentRunner(task)
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._recent_results = [("read_text", "Agent command\n" + task)]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(
            to_text=lambda n=50: 'Window: "Lingdong Script"\nAgent command\n' + task
        ),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": false, "reason": "the requested state is not visible"}'
                        ))
                    )])

    assert runner._verify_done(_Client()) is False
    assert runner._visible_evidence_ledger == []
    assert "not visible" in runner._last_done_reject_reason


def test_text_appears_scoped_to_target_window(monkeypatch):
    from winpilot.action import verify

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(
        verify.fusion,
        "observe",
        lambda *a, **k: SimpleNamespace(elements=[]),
    )
    monkeypatch.setattr(verify.ocr, "find_text", lambda *a, **k: True)

    out = verify._eval_once({"text_appears": "健康科技"}, hwnd=123, level=2)
    assert not out.verified
    assert "未找到文字" in out.detail


def test_text_appears_can_use_fullscreen_without_target(monkeypatch, isolated_config):
    from winpilot.action import verify

    isolated_config({"perception": {"ocr": {"enabled": True}}})
    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 0)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: False)
    monkeypatch.setattr(verify.ocr, "find_text", lambda *a, **k: True)

    out = verify._eval_once({"text_appears": "健康科技"}, hwnd=None, level=2)
    assert out.verified


def test_text_appears_tolerates_ocr_separator_errors(monkeypatch):
    from winpilot.action import verify

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(
        verify.fusion,
        "observe",
        lambda *a, **k: SimpleNamespace(
            elements=[SimpleNamespace(text="FAST WRAP OK 072", source="ocr")]
        ),
    )

    out = verify._eval_once({"text_appears": "FAST_WRAP_OK_072"}, hwnd=123, level=0)

    assert out.verified


def test_text_appears_uses_indexed_uia_query_before_fusion(monkeypatch, isolated_config):
    from winpilot.action import verify

    isolated_config({"perception": {"uia": {"enabled": True}}})
    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(
        verify.uia,
        "find_text",
        lambda hwnd, text: [SimpleNamespace(text="VISUAL_SERVO_OK")],
    )
    monkeypatch.setattr(
        verify.fusion,
        "observe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("indexed UIA hit must skip full fusion")
        ),
    )

    out = verify._eval_once(
        {"text_appears": "VISUAL_SERVO_OK"},
        hwnd=123,
        level=0,
    )

    assert out.verified is True
    assert out.method == "uia-query"


def test_text_appears_respects_disabled_uia_dimension(monkeypatch):
    from winpilot.action import verify

    original_get = verify.CONFIG.get

    def config_get(*path, default=None):
        if path == ("perception", "uia", "enabled"):
            return False
        return original_get(*path, default=default)

    monkeypatch.setattr(verify.CONFIG, "get", config_get)
    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(
        verify.uia,
        "find_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled UIA must not be queried")
        ),
    )
    monkeypatch.setattr(
        verify.fusion,
        "observe",
        lambda *_args, **_kwargs: SimpleNamespace(
            elements=[SimpleNamespace(text="UIA_OFF_RESULT", source="ocr")]
        ),
    )

    out = verify._eval_once(
        {"text_appears": "UIA_OFF_RESULT"},
        hwnd=123,
        level=0,
    )

    assert out.verified is True
    assert out.method == "ocr"


def test_text_appears_uses_async_watcher_roi_ocr_before_full_fusion(monkeypatch):
    import numpy as np

    from winpilot.action import verify
    from winpilot.utils.screenshot import Region

    class Watcher:
        @staticmethod
        def recent_evidence(max_age_ms=1500):
            assert max_age_ms == 1500
            return [(
                np.zeros((48, 180, 3), dtype=np.uint8),
                Region(100, 200, 180, 48),
            )]

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(verify.uia, "find_text", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        verify.ocr,
        "recognize",
        lambda *_args, **_kwargs: [SimpleNamespace(text="CUSTOM ROI DONE")],
    )
    monkeypatch.setattr(
        verify.fusion,
        "observe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("predictive ROI hit must skip full fusion")
        ),
    )

    out = verify.check(
        {"text_appears": "CUSTOM_ROI_DONE"},
        hwnd=123,
        timeout_s=1.0,
        watcher=Watcher(),
    )

    assert out.verified is True
    assert out.method == "predictive-ocr"
    assert out.trace["predictive_ocr"]["started"] is True
    assert out.trace["predictive_ocr"]["ready"] is True
    assert out.trace["predictive_ocr"]["evidence_count"] == 1
    assert out.trace["predictive_ocr"]["processed_count"] == 1


def test_visible_and_groundtruth_checks_overlap_without_early_success(
    monkeypatch,
    isolated_config,
):
    import threading

    from winpilot.action import verify

    isolated_config({"perception": {"uia": {"enabled": True}}})
    groundtruth_started = threading.Event()
    visible_started = threading.Event()

    def check_groundtruth(key, value, timeout_s=15.0):
        assert key == "file_exists"
        assert value == r"C:\demo\result.txt"
        groundtruth_started.set()
        assert visible_started.wait(timeout=1.0)
        return True, "exists"

    def find_visible(_hwnd, _needle):
        visible_started.set()
        assert groundtruth_started.wait(timeout=1.0)
        return [SimpleNamespace(text="VISIBLE RESULT")]

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(verify.groundtruth, "check", check_groundtruth)
    monkeypatch.setattr(verify.uia, "find_text", find_visible)
    monkeypatch.setattr(
        verify.fusion,
        "observe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("indexed UIA evidence should skip full fusion")
        ),
    )

    out = verify.check(
        {
            "text_appears": "VISIBLE_RESULT",
            "file_exists": r"C:\demo\result.txt",
        },
        hwnd=123,
        timeout_s=1.0,
    )

    assert out.verified is True
    assert out.method == "uia-query+groundtruth"
    trace = out.trace["parallel_groundtruth"]
    assert trace["started"] is True
    assert trace["check_count"] == 1
    assert trace["completed_count"] == 1
    assert trace["checks"]["file_exists"]["ok"] is True


def test_parallel_file_evidence_retries_until_eventually_visible(
    monkeypatch,
    isolated_config,
):
    from winpilot.action import verify

    isolated_config({"perception": {"uia": {"enabled": True}}})
    calls = {"count": 0}

    def check_groundtruth(key, value, timeout_s=15.0):
        calls["count"] += 1
        return (
            (True, "exists")
            if calls["count"] >= 3
            else (False, "not created yet")
        )

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(verify.groundtruth, "check", check_groundtruth)
    monkeypatch.setattr(
        verify.uia,
        "find_text",
        lambda _hwnd, _needle: [SimpleNamespace(text="SAVE COMPLETE")],
    )

    out = verify.check(
        {
            "text_appears": "SAVE COMPLETE",
            "file_exists": r"C:\demo\eventual.png",
        },
        hwnd=123,
        timeout_s=1.0,
    )

    assert out.verified is True
    assert calls["count"] == 3
    assert out.trace["parallel_groundtruth"]["checks"]["file_exists"]["attempts"] == 3


def test_image_file_format_rejects_extension_only_fake_success(tmp_path):
    from winpilot.perception import groundtruth

    valid = tmp_path / "valid.png"
    valid.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")
    disguised = tmp_path / "disguised.png"
    disguised.write_bytes(b"\x00\x00\x00\x20ftypmif1" + b"payload")

    assert groundtruth.file_format({"path": str(valid), "format": "png"})[0] is True
    ok, detail = groundtruth.file_format({"path": str(disguised), "format": "png"})
    assert ok is False
    assert "魔数不匹配" in detail


def test_shell_true_verification_is_not_parallelized(monkeypatch, isolated_config):
    from winpilot.action import verify

    isolated_config({"perception": {"uia": {"enabled": True}}})
    order = []

    def find_visible(_hwnd, _needle):
        order.append("visible")
        return [SimpleNamespace(text="VISIBLE RESULT")]

    def check_groundtruth(key, value, timeout_s=15.0):
        order.append(key)
        assert value == "Write-Output true"
        return True, "true"

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(verify.uia, "find_text", find_visible)
    monkeypatch.setattr(verify.groundtruth, "check", check_groundtruth)

    out = verify.check(
        {
            "text_appears": "VISIBLE_RESULT",
            "shell_true": "Write-Output true",
        },
        hwnd=123,
        timeout_s=1.0,
    )

    assert out.verified is True
    assert order == ["visible", "shell_true"]
    assert out.trace["parallel_groundtruth"] == {}


def test_predictive_verify_ignores_roi_from_before_action(monkeypatch):
    import time

    import numpy as np

    from winpilot.action import verify
    from winpilot.utils.screenshot import Region

    state = {"generation": 100}

    class Watcher:
        @staticmethod
        def status():
            return {"last_change_ms": state["generation"]}

        @staticmethod
        def recent_evidence(max_age_ms=1500):
            return [(
                np.zeros((32, 120, 3), dtype=np.uint8),
                Region(10, 20, 120, 32),
            )]

    monkeypatch.setattr(
        verify.ocr,
        "recognize",
        lambda *_args, **_kwargs: [SimpleNamespace(text="NEW RESULT")],
    )
    probe = verify._AsyncTextProbe(Watcher(), after_change_ms=100)

    probe.maybe_start()
    assert probe.to_dict()["started"] is False

    state["generation"] = 101
    probe.maybe_start()
    deadline = time.monotonic() + 0.5
    while not probe.to_dict()["ready"] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert probe.match("NEW_RESULT") is True
    assert probe.to_dict()["generation"] == 101


def test_text_appears_does_not_pass_from_stale_uia_without_post_action_roi(monkeypatch):
    from winpilot.action import verify

    class Watcher:
        @staticmethod
        def status():
            return {"last_change_ms": 100, "last_action_change_ms": 100}

        @staticmethod
        def recent_evidence(max_age_ms=1500):
            return []

        @staticmethod
        def action_evidence(max_age_ms=1500):
            return ([], False)

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(
        verify.uia,
        "find_text",
        lambda *_args, **_kwargs: [SimpleNamespace(text="ALREADY HERE")],
    )

    outcome = verify.check(
        {"text_appears": "ALREADY HERE"},
        hwnd=123,
        timeout_s=0.12,
        watcher=Watcher(),
        watcher_change_baseline=100,
    )

    assert outcome.verified is False
    assert outcome.method == "awaiting-post-action-roi"
    assert outcome.trace["require_changed_text_evidence"] is True


def test_run_with_verify_zero_watcher_generation_rejects_preexisting_uia(monkeypatch):
    from winpilot.action import verify

    class Watcher:
        @staticmethod
        def status():
            return {"last_change_ms": 0, "last_action_change_ms": 0}

        @staticmethod
        def recent_evidence(max_age_ms=1500):
            return []

        @staticmethod
        def action_evidence(max_age_ms=1500):
            return ([], False)

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(
        verify.uia,
        "find_text",
        lambda *_args, **_kwargs: [SimpleNamespace(text="ALREADY HERE")],
    )

    result = verify.run_with_verify(
        "click",
        lambda: {"ok": True},
        expect={"text_appears": "ALREADY HERE"},
        hwnd=123,
        retryable=False,
        timeout_s=0.12,
        watcher=Watcher(),
    )

    assert result["verified"] is False
    assert result["verify_trace"]["require_changed_text_evidence"] is True


def test_run_with_verify_watcher_status_error_rejects_preexisting_uia(monkeypatch):
    from winpilot.action import verify

    class Watcher:
        @staticmethod
        def status():
            raise RuntimeError("watcher unavailable")

        @staticmethod
        def recent_evidence(max_age_ms=1500):
            return []

        @staticmethod
        def action_evidence(max_age_ms=1500):
            return ([], False)

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(
        verify.uia,
        "find_text",
        lambda *_args, **_kwargs: [SimpleNamespace(text="ALREADY HERE")],
    )

    result = verify.run_with_verify(
        "click",
        lambda: {"ok": True},
        expect={"text_appears": "ALREADY HERE"},
        hwnd=123,
        retryable=False,
        timeout_s=0.12,
        watcher=Watcher(),
    )

    assert result["verified"] is False
    assert result["verify_trace"]["require_changed_text_evidence"] is True


def test_predictive_verify_validates_each_evidence_source_generation(monkeypatch):
    import time

    import numpy as np

    from winpilot.action import verify
    from winpilot.utils.screenshot import Region

    class Watcher:
        @staticmethod
        def status():
            return {"last_change_ms": 101, "last_action_change_ms": 99}

        @staticmethod
        def action_evidence(max_age_ms=1500):
            raise AssertionError("stale transient evidence must not be queried")

        @staticmethod
        def recent_evidence(max_age_ms=1500):
            return [(
                np.zeros((32, 120, 3), dtype=np.uint8),
                Region(200, 20, 120, 32),
            )]

    monkeypatch.setattr(
        verify.ocr,
        "recognize",
        lambda *_args, **_kwargs: [SimpleNamespace(text="FRESH RESULT")],
    )
    probe = verify._AsyncTextProbe(
        Watcher(),
        after_change_ms=100,
        needles=["FRESH_RESULT"],
    )

    probe.maybe_start()
    deadline = time.monotonic() + 0.5
    while not probe.to_dict()["ready"] and time.monotonic() < deadline:
        time.sleep(0.01)

    trace = probe.to_dict()
    assert probe.match("FRESH_RESULT") is True
    assert trace["source"] == "watcher-change"
    assert trace["generation"] == 101


def test_predictive_verify_prioritizes_transient_roi_and_stops_after_match(monkeypatch):
    import time

    import numpy as np

    from winpilot.action import verify
    from winpilot.utils.screenshot import Region

    calls = []
    crops = [
        (np.zeros((32, 160, 3), dtype=np.uint8), Region(10, 20, 160, 32)),
        (np.zeros((32, 80, 3), dtype=np.uint8), Region(200, 20, 80, 32)),
    ]

    class Watcher:
        @staticmethod
        def status():
            return {"last_change_ms": 200, "last_action_change_ms": 201}

        @staticmethod
        def action_evidence(max_age_ms=1500):
            return crops, True

        @staticmethod
        def recent_evidence(max_age_ms=1500):
            raise AssertionError("transient action evidence must win")

    def recognize(_image, origin=(0, 0)):
        calls.append(origin)
        return [SimpleNamespace(text="PRESS PULSE ACTIVE T")]

    monkeypatch.setattr(verify.ocr, "recognize", recognize)
    probe = verify._AsyncTextProbe(
        Watcher(),
        after_change_ms=199,
        needles=["PRESS_PULSE_ACTIVE_1"],
    )
    probe.maybe_start()
    deadline = time.monotonic() + 0.5
    while not probe.to_dict()["ready"] and time.monotonic() < deadline:
        time.sleep(0.01)

    trace = probe.to_dict()
    assert probe.match("PRESS_PULSE_ACTIVE_1") is True
    assert trace["source"] == "action-transient"
    assert trace["evidence_count"] == 2
    assert trace["processed_count"] == 1
    assert len(calls) == 1


def test_text_match_rejects_ambiguous_short_digits():
    from winpilot.action.verify import _match_visible_text

    assert not _match_visible_text("4", "http://127.0.0.1:8878\nLOCAL_COUNTER_14")
    assert not _match_visible_text("0", "LOCAL_COUNTER_20")
    assert _match_visible_text("75765", "Calculator display: 75765")
    assert _match_visible_text("LOCAL_COUNTER_4", "LOCAL COUNTER 4")
    assert _match_visible_text("PRESS_PULSE_ACTIVE_1", "PRESS PULSE ACTIVE T")


def test_verify_ignores_unrelated_foreground_when_target_scoped(monkeypatch):
    from winpilot.action import verify

    observed = []

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 200)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(verify.win32gui, "GetAncestor", lambda hwnd, flag: hwnd)
    monkeypatch.setattr(verify.win32gui, "GetWindow", lambda hwnd, flag: 0)

    def observe(hwnd, *args, **kwargs):
        observed.append(hwnd)
        text = "target text" if hwnd == 100 else "wrong foreground text"
        return SimpleNamespace(elements=[SimpleNamespace(text=text, source="win32")])

    monkeypatch.setattr(verify.fusion, "observe", observe)

    out = verify._eval_once({"text_appears": "target text"}, hwnd=100, level=0)

    assert out.verified
    assert observed == [100]


def test_communication_done_requires_final_visible_payload(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner('用qq给我的手机发信息"健康科技"')
    runner.session.hwnd = 123
    runner.result_text = '任务完成: 已发送"健康科技"'
    runner._recent_results = [
        ("type_text", '{"ok": true, "verified": true}'),
        ("hotkey", '{"ok": true, "keys": ["enter"]}'),
    ]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "QQ"\n会话列表\n我的手机'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_input_done_requires_required_visible_text(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "打开 Sticky Notes (new)，点击新建笔记，输入两行：第一行=锚定修复测试，第二行=状态=完成。"
    )
    runner.session.hwnd = 123
    runner.result_text = "已完成"
    runner._recent_results = [
        ("read_text", "便笺\n新建笔记\n最近的笔记"),
    ]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Sticky Notes (new)"\n便笺\n最近的笔记'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_required_visible_payloads_extracts_spoken_line_format():
    from winpilot.agent.task_rules import _required_visible_payloads

    task = (
        "打开记事本，输入两行文字：第一行 WinPilot E2E 记录，"
        "第二行 时间测试通过。然后通过GUI另存为桌面上的 WinPilotE2E_note.txt。"
    )

    assert _required_visible_payloads(task) == [
        "WinPilot E2E 记录",
        "时间测试通过",
    ]


def test_gui_only_shell_policy():
    from winpilot.agent.loop import (
        _gui_only_forbids_shell,
        _gui_only_launch_uses_shell,
        _gui_only_shell_allowed,
        _is_gui_only_task,
    )

    assert _is_gui_only_task("GUI-only test. Use Quill Editor.")
    visible_drawing = "Use Aurora Canvas to draw a colorful fictional creature."
    assert not _is_gui_only_task(visible_drawing)
    assert not _gui_only_forbids_shell(visible_drawing)
    assert not _is_gui_only_task("Write a Python program that draws an image.")
    assert _is_gui_only_task("Use GUI only; do not use shell.")
    assert _is_gui_only_task("请只用GUI完成这个任务")
    assert _is_gui_only_task("\u5fc5\u987b\u901a\u8fc7\u771f\u5b9eGUI\u64cd\u4f5c")
    assert _is_gui_only_task("\u5168\u7a0b\u4e3b\u8981\u64cd\u4f5c\u5fc5\u987b\u8d70GUI")
    assert _is_gui_only_task("\u5fc5\u987b\u901a\u8fc7GUI\u64cd\u4f5c\uff0c\u4e0d\u8981\u7528\u547d\u4ee4\u884c")
    assert _is_gui_only_task("GUI方式测试：打开画图，用鼠标拖动画布。")
    assert _is_gui_only_task("必须通过可见GUI操作，不要用脚本生成图片。")
    assert _gui_only_shell_allowed(
        'Test-Path "C:\\Users\\zou\\Desktop\\x.txt"; Get-Content "C:\\Users\\zou\\Desktop\\x.txt"'
    )
    assert not _gui_only_shell_allowed("Start-Process calc.exe")
    assert not _gui_only_shell_allowed("Set-Content C:\\Users\\zou\\Desktop\\x.txt hello")
    assert not _gui_only_shell_allowed("powershell -ExecutionPolicy Bypass -File C:\\Users\\zou\\gui_final.ps1")
    assert not _gui_only_shell_allowed(
        'Test-Path "C:\\Users\\zou\\Desktop\\x.txt"; python C:\\Users\\zou\\gui_final.py'
    )
    assert _gui_only_shell_allowed('Get-Item "C:\\Users\\zou\\Desktop\\x.txt"')
    assert _gui_only_forbids_shell("必须通过真实GUI操作，不要用shell")
    assert _gui_only_forbids_shell("Use GUI only; do not use shell.")
    assert _gui_only_forbids_shell("Use GUI only; no shell.")
    assert _gui_only_forbids_shell("请只用GUI完成这个任务")
    assert _gui_only_forbids_shell("GUI only, complete it visibly.")
    assert _gui_only_launch_uses_shell("powershell -NoExit -Command python -m http.server 8878")
    assert not _gui_only_launch_uses_shell("calc")


def test_tool_schemas_can_hide_shell_for_strict_gui_only():
    from winpilot.agent.tool_schemas import tool_schemas

    names = {tool["function"]["name"] for tool in tool_schemas(allow_shell=False)}

    assert "run_shell" not in names
    assert "run_detached" not in names
    assert "click" in names
    assert "run_action_batch" in names


def test_window_title_expect_condition(monkeypatch):
    from winpilot.action import verify

    monkeypatch.setattr(verify.win32gui, "GetForegroundWindow", lambda: 456)
    monkeypatch.setattr(verify.win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(verify.win32, "_window_text", lambda hwnd: "WinPilotDeep_append.txt - Notepad")

    out = verify._eval_once({"window_title": "WinPilotDeep_append.txt"}, hwnd=123, level=0)

    assert out.verified


def test_coordinate_click_keeps_pixel_authored_point_by_default(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    fake_executor = SimpleNamespace(
        uia_invoke=lambda element: calls.append(("invoke", element.elem_id)) or True,
        click=lambda x, y, **_kwargs: calls.append(("click", x, y)) or {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)
    session = ToolSession(task="activate the visible control")
    session.snapshot = Snapshot(
        hwnd=1,
        title="Arbitrary application",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(
            elem_id=12,
            role="link",
            text="provider-only target",
            cx=110,
            cy=100,
            left=90,
            top=90,
            width=40,
            height=20,
            source="uia",
        )],
    )

    result = session._t_click({"x": 93, "y": 92})

    assert calls == [("click", 93, 92)]
    assert result.replay["coords"] == [93, 92]
    assert "semantic_hit_snap" not in result.replay


def test_coordinate_click_snaps_to_fresh_actionable_semantic_hit(
    monkeypatch,
    coordinate_semantic_snap_enabled,
):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    fake_executor = SimpleNamespace(
        uia_invoke=lambda element: calls.append(("invoke", element.elem_id)) or True,
        click=lambda *args, **kwargs: calls.append(("click", args, kwargs)) or {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)
    session = ToolSession(task="activate the visible control")
    session.snapshot = Snapshot(
        hwnd=1,
        title="Arbitrary application",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(
            elem_id=12,
            role="link",
            text="javascript:;",
            cx=110,
            cy=100,
            left=90,
            top=90,
            width=40,
            height=20,
            source="uia",
        )],
    )

    result = session._t_click({"x": 93, "y": 92})
    payload = json.loads(result.text)

    assert calls == [("invoke", 12)]
    assert payload["via"] == "semantic_hit_uia_invoke"
    assert payload["semantic_hit_snap"]["authored_coords"] == [93, 92]
    assert payload["semantic_hit_snap"]["snapped_coords"] == [110, 100]
    assert result.replay["coords"] == [110, 100]


def test_coordinate_click_does_not_snap_to_broad_blank_container(
    monkeypatch,
    coordinate_semantic_snap_enabled,
):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    fake_executor = SimpleNamespace(
        uia_invoke=lambda element: calls.append(("invoke", element.elem_id)) or True,
        click=lambda x, y, **_kwargs: calls.append(("click", x, y)) or {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)
    session = ToolSession(task="activate the small visible affordance under the pointer")
    session.snapshot = Snapshot(
        hwnd=1,
        title="Arbitrary feed",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(
            elem_id=50,
            role="listitem",
            text="",
            cx=200,
            cy=150,
            left=0,
            top=0,
            width=400,
            height=300,
            source="uia",
        )],
    )

    result = session._t_click({"x": 350, "y": 250})

    assert calls == [("click", 350, 250)]
    assert result.replay["coords"] == [350, 250]
    assert "semantic_hit_snap" not in result.replay


def test_coordinate_click_can_locally_refine_high_confidence_ocr_text(
    monkeypatch,
    coordinate_semantic_snap_enabled,
):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    fake_executor = SimpleNamespace(
        uia_invoke=lambda _element: False,
        click=lambda x, y, **_kwargs: calls.append((x, y)) or {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)
    session = ToolSession(task="activate the compact visible text control")
    session.snapshot = Snapshot(
        hwnd=1,
        title="Owner-drawn application chrome",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(
            elem_id=17,
            role="text",
            text="Visible action",
            cx=120,
            cy=100,
            left=100,
            top=90,
            width=40,
            height=20,
            source="ocr",
            confidence=0.92,
        )],
    )

    result = session._t_click({"x": 96, "y": 100})

    assert calls == [(120, 100)]
    assert result.replay["semantic_hit_snap"]["source"] == "ocr"
    assert result.replay["semantic_hit_snap"]["authored_coords"] == [96, 100]
    assert result.replay["coords"] == [120, 100]


def test_coordinate_click_preserves_subtarget_inside_plain_ocr_row(
    monkeypatch,
    coordinate_semantic_snap_enabled,
):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    fake_executor = SimpleNamespace(
        uia_invoke=lambda _element: False,
        click=lambda x, y, **_kwargs: calls.append((x, y)) or {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)
    session = ToolSession(task="activate one visible action in a compact action row")
    session.snapshot = Snapshot(
        hwnd=1,
        title="Arbitrary owner-drawn feed",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(
            elem_id=18,
            role="text",
            text="First action   Second action   Third action",
            cx=190,
            cy=100,
            left=100,
            top=90,
            width=180,
            height=20,
            source="ocr",
            confidence=0.94,
        )],
    )

    result = session._t_click({"x": 112, "y": 100})

    assert calls == [(112, 100)]
    assert result.replay["coords"] == [112, 100]
    assert "semantic_hit_snap" not in result.replay


def test_coordinate_click_projects_near_broad_ocr_row_without_recentering(
    monkeypatch,
    coordinate_semantic_snap_enabled,
):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    fake_executor = SimpleNamespace(
        uia_invoke=lambda _element: False,
        click=lambda x, y, **_kwargs: calls.append((x, y)) or {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)
    session = ToolSession(task="activate the first visible action in a merged row")
    session.snapshot = Snapshot(
        hwnd=1,
        title="Arbitrary owner-drawn feed",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(
            elem_id=19,
            role="text",
            text="First action   Second action   Third action",
            cx=190,
            cy=100,
            left=100,
            top=90,
            width=180,
            height=20,
            source="ocr",
            confidence=0.94,
        )],
    )

    result = session._t_click({"x": 96, "y": 100})

    assert calls == [(103, 100)]
    assert result.replay["coords"] == [103, 100]
    assert result.replay["semantic_hit_snap"]["mode"] == (
        "nearest_plain_ocr_subtarget"
    )


def test_coordinate_click_keeps_owner_drawn_scene_at_authored_point(
    monkeypatch,
    coordinate_semantic_snap_enabled,
):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    fake_executor = SimpleNamespace(
        uia_invoke=lambda element: calls.append(("invoke", element.elem_id)) or True,
        click=lambda x, y, **kwargs: calls.append(("click", x, y)) or {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)
    session = ToolSession(task="interact with the visible game board")
    session.snapshot = Snapshot(
        hwnd=1,
        title="Owner-drawn scene",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(
            elem_id=7,
            role="image",
            text="game surface",
            cx=400,
            cy=300,
            left=0,
            top=0,
            width=800,
            height=600,
            source="cv",
        )],
    )

    result = session._t_click({"x": 123, "y": 234})

    assert calls == [("click", 123, 234)]
    assert result.replay["coords"] == [123, 234]
    assert "semantic_hit_snap" not in result.replay


def test_power_task_allows_power_labeled_click(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    fake_executor = SimpleNamespace(
        uia_invoke=lambda element: False,
        click=lambda *args, **kwargs: {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda name, action, **kwargs: action(),
        post_state_summary=lambda: {},
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)

    session = ToolSession(task="打开电源设置查看电池状态")
    session.snapshot = Snapshot(
        hwnd=1,
        title="DesktopWindowXamlSource",
        region=Region(0, 0, 1920, 1080),
        elements=[
            UIElement(
                elem_id=18,
                role="button",
                text="电源 电池状态: 95% 可用(电源已接通)",
                cx=1768,
                cy=1050,
                left=1751,
                top=1020,
                width=35,
                height=60,
                source="uia",
            )
        ],
    )

    result = session._t_click({"element_id": 18})

    assert "Refused to click" not in result.text


def test_physical_click_bypasses_uia_invoke(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    click_kwargs = []
    fake_executor = SimpleNamespace(
        uia_invoke=lambda element: calls.append("uia") or True,
        click=lambda *args, **kwargs: (
            calls.append("mouse"), click_kwargs.append(kwargs), {"ok": True}
        )[-1],
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda name, action, **kwargs: action(),
        post_state_summary=lambda: {},
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)

    session = ToolSession(task="test the visible pressed state")
    session.snapshot = Snapshot(
        hwnd=1,
        title="Target",
        region=Region(0, 0, 500, 400),
        elements=[UIElement(
            elem_id=4, role="button", text="Press pulse",
            cx=120, cy=80, left=80, top=60, width=80, height=40, source="uia",
        )],
    )

    session._t_click({"element_id": 4, "physical": True})

    assert calls == ["mouse"]
    assert click_kwargs[0]["force_physical"] is True


def test_type_text_reuses_last_clicked_input(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []

    fake_executor = SimpleNamespace(
        uia_invoke=lambda element: False,
        click=lambda *args, **kwargs: {"ok": True},
        uia_set_value=lambda element, text: calls.append((element.elem_id, text)) or True,
        type_text=lambda text, hwnd=None: {"ok": True, "typed": len(text)},
        focus_window=lambda hwnd: {"ok": True},
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)

    session = ToolSession(task="Save this file through GUI")
    session.hwnd = 10
    session.snapshot = Snapshot(
        hwnd=10,
        title="Save As",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(elem_id=3, role="edit", text="File name:=*.txt", cx=100, cy=100, source="uia")],
    )

    session._t_click({"element_id": 3})
    result = session._t_type_text({"text": "ordinary input"})

    assert calls == [(3, "ordinary input")]
    assert "uia_set_value" in result.text


def test_type_text_uses_physical_keyboard_for_composite_file_input(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    calls = []
    fake_executor = SimpleNamespace(
        uia_invoke=lambda element: False,
        click=lambda *args, **kwargs: calls.append(("click", args)) or {"ok": True},
        uia_set_value=lambda element, text: calls.append(("set", text)) or True,
        type_text=lambda text, hwnd=None: calls.append(("type", text, hwnd)) or {"ok": True},
        focus_window=lambda hwnd: {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)
    monkeypatch.setattr(tools_mod.win32gui, "GetForegroundWindow", lambda: 10)

    session = ToolSession(task="Save this document through the GUI")
    session.hwnd = 10
    session.snapshot = Snapshot(
        hwnd=10,
        title="Save As",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(
            elem_id=3, role="combobox", text="File name:=draft.docx",
            cx=100, cy=100, source="uia",
        )],
    )

    session._t_click({"element_id": 3})
    result = session._t_type_text({"text": r"C:\Users\zou\Desktop\x.docx"})

    assert not any(call[0] == "set" for call in calls)
    assert ("type", r"C:\Users\zou\Desktop\x.docx", 10) in calls
    assert '"via": "uia_set_value"' not in result.text


def test_hotkey_printable_sequence_is_typed_as_text(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession

    calls = []

    fake_executor = SimpleNamespace(
        type_text=lambda text, hwnd=None: calls.append(("type_text", text, hwnd)) or {"ok": True, "typed": len(text)},
        press_keys=lambda keys: calls.append(("press_keys", keys)) or {"ok": True},
        focus_window=lambda hwnd: {"ok": True},
        uia_set_value=lambda element, text: False,
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)

    session = ToolSession(task="enter calculator expression")
    result = session._t_hotkey({"keys": ["6", "+", "7", "="]})

    assert len(calls) == 1
    assert calls[0][0] == "type_text"
    assert calls[0][1] == "6+7="
    assert "coerced_from_hotkey" in result.text


def test_hotkey_json_string_keys_are_pressed_not_typed(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession

    calls = []

    fake_executor = SimpleNamespace(
        type_text=lambda text, hwnd=None: calls.append(("type_text", text, hwnd)) or {"ok": True, "typed": len(text)},
        press_keys=lambda keys: calls.append(("press_keys", keys)) or {"ok": True, "keys": keys},
        focus_window=lambda hwnd: {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)

    session = ToolSession(task="save the file")
    result = session._t_hotkey({"keys": '["ctrl", "s"]}'})

    assert calls == [("press_keys", ["ctrl", "s"])]
    assert '"keys": ["ctrl", "s"]' in result.text
    assert "coerced_from_hotkey" not in result.text


def test_copy_hotkey_is_verified_by_clipboard_generation(monkeypatch):
    import json

    import winpilot.agent.interaction_tools as interaction_tools
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession

    sequences = iter([41, 42])
    fake_executor = SimpleNamespace(
        press_keys=lambda keys: {"ok": True, "keys": keys},
        focus_window=lambda hwnd: {"ok": True},
        clipboard_read=lambda: "https://example.test/actual-address",
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)
    monkeypatch.setattr(
        interaction_tools,
        "_clipboard_sequence_number",
        lambda: next(sequences),
    )

    result = ToolSession(task="copy the selected browser address")._t_hotkey(
        {"keys": ["c", "ctrl"]}
    )
    payload = json.loads(result.text)

    assert payload["keys"] == ["ctrl", "c"]
    assert payload["clipboard_sequence_changed"] is True
    assert payload["clipboard_text_length"] > 0
    assert payload["verified"] is True


def test_assistant_history_truncates_verbose_content():
    from winpilot.agent.loop import _sanitize_assistant

    msg = SimpleNamespace(content="x" * 2000, tool_calls=[])

    out = _sanitize_assistant(msg)

    assert len(out["content"]) < 1100
    assert "full response is kept in the LLM trace" in out["content"]


def test_gui_eval_policy_flags_scripted_gui_bypass():
    from scripts.gui_eval_run import _run_policy_checks

    case = {"task": "\u5168\u7a0b\u4e3b\u8981\u64cd\u4f5c\u5fc5\u987b\u8d70GUI"}

    readonly = _run_policy_checks([
        {
            "type": "agent_tool",
            "tool": "run_shell",
            "args": {"command": 'Test-Path "C:\\Users\\zou\\Desktop\\x.txt"; Get-Content "C:\\Users\\zou\\Desktop\\x.txt"'},
        }
    ], case)
    scripted = _run_policy_checks([
        {"type": "agent_tool", "tool": "write_file", "args": {"path": "gui_task.py"}},
        {
            "type": "agent_tool",
            "tool": "run_shell",
            "args": {"command": "python C:\\Users\\zou\\gui_task.py"},
        },
    ], case)

    assert readonly["passed"]
    assert not scripted["passed"]
    assert len(scripted["violations"]) == 2


def test_gui_eval_request_sends_control_token_without_serializing_it(monkeypatch):
    from scripts import gui_eval_run

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(gui_eval_run.urllib.request, "urlopen", fake_urlopen)

    result = gui_eval_run._request_json(
        "POST",
        "http://127.0.0.1:8765/api/chat",
        {"task": "test"},
        token="secret-control-token",
    )

    assert result == {"ok": True}
    assert captured["request"].get_header("Authorization") == "Bearer secret-control-token"
    assert b"secret-control-token" not in captured["request"].data


def test_drawing_done_requires_drag(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a house on a canvas and save as C:\\Users\\zou\\Desktop\\x.png")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._recent_results = [("click", '{"ok": true}')]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Paint"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_drawing_done_requires_requested_image_file(monkeypatch, tmp_path):
    from winpilot.agent.loop import AgentRunner

    missing = str(tmp_path / "missing.png")
    runner = AgentRunner(f"draw a house on a canvas and save as {missing}")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['drag({"x1": 1, "y1": 1, "x2": 20, "y2": 20})']
    runner._recent_results = [("drag", '{"ok": true, "visual_change_detected": true}')]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Paint"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_drawing_done_requires_requested_colors(monkeypatch, tmp_path):
    import cv2
    import numpy as np

    from winpilot.agent.loop import AgentRunner

    out = tmp_path / "black_only.png"
    image = np.full((120, 160, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 20), (100, 80), (0, 0, 0), 3)
    cv2.imwrite(str(out), image)

    runner = AgentRunner(f"draw a red sun and blue house on a canvas and save as {out}")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['drag({"x1": 1, "y1": 1, "x2": 20, "y2": 20})']
    runner._recent_results = [("drag", '{"ok": true, "visual_change_detected": true}')]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Paint"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_simple_drawing_done_requires_fresh_goal_conditioned_visual_judge(
    monkeypatch,
    mocked_visual_judge_capability,
):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a house outline on the canvas")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = [
        'draw_strokes({"strokes": [[[10, 10], [50, 50]]], "duration": 0.3})'
    ]
    runner._recent_results = [
        ("draw_strokes", '{"ok": true, "visual_change_detected": true}'),
    ]

    def fresh_native_screen():
        runner._fresh_done_image_b64 = "fresh-drawing-frame"
        return 'Window: "Paint"'

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_native_screen)
    calls = []

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **kwargs):
                    calls.append(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "fresh frame shows a house outline"}'
                        ))
                    )])

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True
    assert calls
    assert isinstance(calls[0]["messages"][1]["content"], list)


def test_simple_drawing_pixel_change_does_not_bypass_visual_judge(
    monkeypatch,
    mocked_visual_judge_capability,
):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a triangle on the canvas")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = [
        'run_action_batch({"actions": [{"action": "draw_strokes", "strokes": [[[10, 10], [50, 50]]]}]})'
    ]
    runner._recent_results = [
        ("run_action_batch", '{"ok": true, "verified": true, "results": [{"action": "draw_strokes", "preview": "{\\"visual_change_detected\\": true}"}]}'),
    ]

    def fresh_native_screen():
        runner._fresh_done_image_b64 = "fresh-drawing-frame"
        return 'Window: "Paint"'

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_native_screen)

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": false, "reason": "frame shows a line, not a triangle"}'
                        ))
                    )])

    assert runner._verify_done(_Client()) is False
    assert runner._done_verified is False
    assert "not a triangle" in runner._last_done_reject_reason


def test_simple_drawing_compacted_action_evidence_still_requires_fresh_judge(
    monkeypatch,
    mocked_visual_judge_capability,
):
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a triangle on the canvas")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = [
        'run_action_batch({"actions": [{"action": "click", "x": 1, "y": 2}, '
        '{"action": "draw_strokes", "strokes": [[[10, 10], [50, 50]]]}]})'
    ]
    raw_result = json.dumps({
        "ok": True,
        "verified": True,
        "results": [
            {
                "action": "click",
                "ok": True,
                "preview": "x" * 900,
            },
            {
                "action": "draw_strokes",
                "ok": True,
                "data": {
                    "ok": True,
                    "verified": True,
                    "visual_change_detected": True,
                    "visual_change_ratio": 0.04,
                    "verify_method": "cv-diff",
                },
            },
        ],
    })
    compact = runner._compact_tool_evidence("run_action_batch", raw_result)
    runner._recent_results = [("run_action_batch", compact)]

    def fresh_native_screen():
        runner._fresh_done_image_b64 = "fresh-drawing-frame"
        return 'Window: "Paint"'

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_native_screen)
    calls = []

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **kwargs):
                    calls.append(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "fresh frame shows the triangle"}'
                        ))
                    )])

    assert "visual_change_detected" in compact
    assert len(compact) < 900
    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True
    assert calls


def test_simple_drawing_without_fresh_or_semantic_frame_fails_closed(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a house outline on the canvas")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['drag({"x1": 10, "y1": 10, "x2": 50, "y2": 50})']
    runner._recent_results = [
        ("drag", '{"ok": true, "visual_change_detected": true}'),
    ]

    def unavailable_native_screen():
        runner._fresh_done_image_b64 = ""
        return 'Window: "Paint"'

    monkeypatch.setattr(runner, "_fresh_done_screen", unavailable_native_screen)

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **_kwargs):
                    raise AssertionError("drawing without semantic visual evidence reached judge")

    assert runner._verify_done(_Client()) is False
    assert "pixel change alone" in runner._last_done_reject_reason


def test_saved_drawing_metadata_does_not_bypass_fresh_semantic_judge(
    monkeypatch,
    mocked_visual_judge_capability,
):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a triangle on the canvas")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = [
        'draw_strokes({"strokes": [[[10, 10], [50, 50]]]})'
    ]
    runner._recent_results = [
        ("draw_strokes", '{"ok": true, "visual_change_detected": true}'),
    ]

    def fresh_native_screen():
        runner._fresh_done_image_b64 = "fresh-drawing-frame"
        return 'Window: "Paint"'

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_native_screen)
    monkeypatch.setattr(
        runner,
        "_deterministic_saved_file_verdict",
        lambda *_args: (True, "saved file exists and is fresh"),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": false, "reason": "saved canvas does not show a triangle"}'
                        ))
                    )])

    assert runner._verify_done(_Client()) is False
    assert runner._done_verified is False
    assert "does not show a triangle" in runner._last_done_reject_reason


def test_top_level_draw_strokes_uses_fast_controller_visual_verify():
    import json

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("draw a triangle on the canvas")
    calls = []

    def fake_dispatch(name, args):
        calls.append((name, args))
        assert name == "run_action_batch"
        return ToolResult(json.dumps({
            "ok": True,
            "verified": True,
            "needs_observe": False,
            "last_action": "auto_visual_verify",
            "fast_path_advice": (
                "The controller already verified the visible local change for the last draw/drag action. "
                "For a simple visible manipulation task, call done instead of adding another zoom/observe "
                "round unless the user explicitly needs semantic inspection."
            ),
            "metrics": {"action_count": 1, "observe_count": 1},
            "results": [
                {
                    "index": 1,
                    "action": "draw_strokes",
                    "ok": True,
                    "verified": True,
                    "data": {
                        "ok": True,
                        "visual_change_detected": True,
                        "visual_change_ratio": 0.031,
                    },
                },
                {
                    "index": 2,
                    "action": "auto_visual_verify",
                    "ok": True,
                    "verified": True,
                    "data": {
                        "ok": True,
                        "source_action": "draw_strokes",
                        "verified": True,
                        "visual_change_detected": True,
                        "visual_change_ratio": 0.031,
                        "nonvisual_summary": {"blankish": False, "ink_ratio": 0.031},
                    },
                },
            ],
        }))

    runner.session.dispatch = fake_dispatch
    args = {"strokes": [[[10, 10], [50, 50]]], "duration": 0.1}
    terminal = runner._handle_tool_calls([
        SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="draw_strokes", arguments=json.dumps(args)),
        )
    ])

    assert terminal is None
    assert calls == [("run_action_batch", {"actions": [{"action": "draw_strokes", **args}], "sync": False})]
    assert runner._had_verified_action is True
    assert runner._last_verify_ok is True
    assert runner._recent_results[-1][0] == "draw_strokes"
    assert "visual_change_detected" in runner._recent_results[-1][1]
    assert len(runner._recent_results[-1][1]) < 1600
    assert "results" not in json.loads(runner._recent_results[-1][1])
    payload = json.loads(runner.messages[-1]["content"])
    assert payload["wrapped_by_fast_controller"] is True
    assert payload["verified"] is True
    assert "call done instead of adding another zoom/observe" in payload["fast_path_advice"]
    assert payload["auto_visual_verify"]["nonvisual_summary"]["blankish"] is False


def test_execute_subgoal_compact_evidence_keeps_fast_path_advice():
    import json

    from winpilot.agent.loop import AgentRunner

    raw = json.dumps({
        "ok": True,
        "verified": True,
        "subgoal": "click refresh",
        "subgoal_route": "controller_first",
        "controller_state": {"phase": "verified", "needs_llm_reason": False},
        "fast_path_advice": "Controller already verified matched_texts; call done.",
        "evidence_packet": {
            "matched_texts": ["READY_STATE"],
            "missing_texts": [],
            "candidate_click_labels": ["Refresh status"],
        },
        "metrics": {"action_count": 2},
        "actions_executed": [{"action": "click_element"}, {"action": "observe"}],
    })

    compact = json.loads(AgentRunner._compact_tool_evidence("execute_subgoal", raw))

    assert compact["verified"] is True
    assert compact["matched_texts"] == ["READY_STATE"]
    assert compact["candidate_click_labels"] == ["Refresh status"]
    assert "call done" in compact["fast_path_advice"]


def test_complete_subgoal_tool_history_is_compacted_for_llm():
    import json

    from winpilot.agent.loop import AgentRunner

    raw = """已完成子目标 2（已核验）。
当前计划(todo)：
[x] 1. 打开 Windows 画图应用
[x] 2. 在画布上绘制一个蓝色三角形
[ ] 3. 在蓝色三角形右边绘制一个红色矩形
[ ] 4. 通过可见画面确认两个图形和颜色都存在
完成一项就调用 complete_subgoal(index=序号)；计划不合适就调用 revise_plan 重列。
Plan hint: this todo is already active; do not call revise_plan just to restate it."""

    content = AgentRunner._compact_tool_evidence("complete_subgoal", raw)
    payload = json.loads(content)

    assert len(content) < 700
    assert payload["completed_index"] == 2
    assert payload["verified"] is True
    assert payload["next_unfinished"]["index"] == 3
    assert payload["remaining_steps"][0]["goal"].startswith("在蓝色三角形右边")
    assert "guidance" not in payload
    assert "Plan hint:" not in content


def test_complete_subgoal_append_preserves_raw_trace_but_compacts_message():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw shapes")
    raw = "\n".join([
        "已完成子目标 1。",
        "当前计划(todo)：",
        "[x] 1. 打开应用",
        "[ ] 2. 绘图",
        "Plan hint: this todo is already active; do not call revise_plan just to restate it.",
    ])

    runner._append_tool_result("call-plan", raw, tool_name="complete_subgoal")

    assert len(runner.messages[-1]["content"]) < 700
    assert '"next_unfinished"' in runner.messages[-1]["content"]
    assert "Plan hint:" not in runner.messages[-1]["content"]




def test_compact_execute_subgoal_keeps_draw_actions_before_plan_summary():
    import json

    from winpilot.agent.loop import AgentRunner

    raw = json.dumps({
        "ok": True,
        "verified": True,
        "subgoal": "draw shapes",
        "subgoal_route": "controller_first",
        "actions_executed": [
            {"action": "draw_strokes", "shape": "triangle", "requested_color": "blue"},
            {"action": "draw_strokes", "shape": "rectangle", "requested_color": "red"},
        ],
        "evidence_packet": {
            "matched_visual_colors": ["blue", "red"],
            "missing_visual_colors": [],
        },
        "auto_completed_plan_items": [
            {"index": 2, "goal": "draw blue triangle"},
            {"index": 3, "goal": "draw red rectangle"},
        ],
        "plan_after_auto_complete": "x" * 2000,
    })

    compact = AgentRunner._compact_tool_evidence("execute_subgoal", raw)

    assert '"action": "draw_strokes"' in compact
    assert "auto_completed_plan_items" in compact




def test_read_text_tracks_active_plan_visible_check_with_screen_epoch():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.planner import Plan

    runner = AgentRunner("type a report in the visible document")
    runner.session.hwnd = 42
    runner.session._screen_epoch = 9
    runner.session.plan = Plan([{
        "goal": "type the first paragraph",
        "check": {"text_appears": "办公自动化验收报告"},
    }])

    runner._track_visible_evidence(
        "read_text",
        {"hwnd": 42},
        "工具栏\n办公自动化验收报告\n正文",
    )

    assert runner._visible_evidence_ledger[-1]["target"] == "办公自动化验收报告"
    assert runner._visible_evidence_ledger[-1]["source"] == "read_text"
    assert runner._visible_evidence_ledger[-1]["screen_epoch"] == 9


def test_done_gate_sees_nested_execute_subgoal_draw_strokes():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a triangle on the canvas")
    runner._steps_log = ["execute_subgoal \"draw triangle\""]
    runner._recent_results = [
        ("execute_subgoal", json.dumps({
            "actions_executed": [{"action": "draw_strokes"}],
            "results": [{"action": "draw_strokes", "data": {"visual_change_detected": True}}],
        })),
    ]

    assert runner._history_mentions_action("draw_strokes") is True


def test_spatial_done_uses_fresh_goal_judge_not_color_counters(
    monkeypatch,
    mocked_visual_judge_capability,
):
    import json

    from winpilot.agent.loop import AgentRunner

    task = "draw a blue triangle on the visible canvas"
    runner = AgentRunner(task)
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = [
        'execute_subgoal({"draw": {"shape": "triangle", "color": "blue"}})'
    ]
    runner._recent_results = [(
        "execute_subgoal",
        json.dumps({
            "ok": True,
            "verified": False,
            "actions_executed": [{"action": "draw_strokes"}],
            "evidence_packet": {
                "matched_visual_colors": [],
                "missing_visual_colors": ["blue"],
            },
            "results": [{
                "action": "draw_strokes",
                "data": {"visual_change_detected": True},
            }],
        }),
    )]
    runner._fresh_done_image_b64 = "fresh-goal-frame"
    monkeypatch.setattr(runner, "_fresh_done_screen", lambda: 'Window: "Paint"')
    requests = []

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **kwargs):
                    requests.append(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps({
                            "done": True,
                            "reason": "the fresh frame satisfies the original user task",
                        }))
                    )])

    assert runner._verify_done(_Client(), steps_left=0) is True
    assert runner._done_verified is True
    assert requests
    user_content = requests[0]["messages"][1]["content"]
    prompt = next(part["text"] for part in user_content if part.get("type") == "text")
    assert task in prompt
    assert any(part.get("type") == "image_url" for part in user_content)


def test_spatial_done_without_fresh_frame_fails_closed_despite_controller_metadata(
    monkeypatch,
):
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("create a detailed illustration in the visible canvas")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['execute_subgoal({"draws": [...]})']
    result = json.dumps({
        "ok": True,
        "verified": True,
        "structured_scene_verified": True,
        "actions_executed": [{"action": "draw_strokes"}],
        "evidence_packet": {
            "matched_visual_colors": ["orange", "green", "purple"],
            "missing_visual_colors": [],
            "scene_structure": {
                "structured_scene_verified": True,
                "primitive_count": 30,
            },
        },
        "visual_change_detected": True,
    })
    runner._recent_results = [("execute_subgoal", result)]
    runner._tool_history = [("execute_subgoal", {"draws": [{}] * 30}, result)]
    monkeypatch.setattr(runner, "_fresh_done_screen", lambda: 'Window: "Paint"')

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **_kwargs):
                    raise AssertionError("judge must not run without a fresh semantic frame")

    assert runner._verify_done(_Client()) is False
    assert "no fresh goal-conditioned frame" in runner._last_done_reject_reason


def test_top_level_click_uses_fast_controller_without_explicit_batch():
    import json

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("open the visible menu")
    calls = []

    def fake_dispatch(name, args):
        calls.append((name, args))
        assert name == "run_action_batch"
        return ToolResult(json.dumps({
            "ok": True,
            "needs_observe": True,
            "last_action": "click",
            "metrics": {"action_count": 1, "screen_wait_count": 1},
            "results": [
                {
                    "index": 1,
                    "action": "click",
                    "ok": True,
                    "data": {"ok": True, "verified": False},
                },
                {
                    "index": 2,
                    "action": "sync_stable",
                    "ok": True,
                    "data": {"ok": True, "stable": True},
                },
            ],
        }))

    runner.session.dispatch = fake_dispatch
    out = runner._dispatch_with_fast_visual_wrap("click", {"x": 10, "y": 20})
    payload = json.loads(out.text)

    assert calls == [("run_action_batch", {"actions": [{"action": "click", "x": 10, "y": 20}]})]
    assert payload["wrapped_by_fast_controller"] is True
    assert payload["source_action"] == "click"
    assert payload["needs_observe"] is True
    assert payload["metrics"]["screen_wait_count"] == 1


def test_top_level_pointer_tools_preserve_one_cross_frame_controller_session():
    import json

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("complete the visible interaction")
    held = {"button": ""}
    calls = []

    runner.session._pointer_hold_status = lambda: {
        "active": bool(held["button"]),
        "button": held["button"],
    }

    def fake_dispatch(name, args):
        calls.append((name, args))
        pointer = dict(args.get("pointer_session") or {})
        held["button"] = str(pointer.get("after") or "")
        return ToolResult(json.dumps({
            "ok": True,
            "verified": True,
            "pointer_session": {
                "active": bool(held["button"]),
                "button": held["button"],
                **pointer,
            },
            "results": [
                {
                    "action": args["actions"][0]["action"],
                    "ok": True,
                    "verified": True,
                    "data": {"ok": True},
                },
            ],
        }))

    runner.session.dispatch = fake_dispatch

    down = json.loads(runner._dispatch_with_fast_visual_wrap(
        "mouse_down",
        {"x": 10, "y": 20, "button": "left"},
    ).text)
    runner._dispatch_with_fast_visual_wrap("move_mouse", {"x": 30, "y": 40})
    up = json.loads(runner._dispatch_with_fast_visual_wrap(
        "mouse_up",
        {"x": 50, "y": 60},
    ).text)

    assert [item[1]["pointer_session"] for item in calls] == [
        {"before": "", "after": "left"},
        {"before": "left", "after": "left"},
        {"before": "left", "after": ""},
    ]
    assert down["pointer_session"]["active"] is True
    assert up["pointer_session"]["active"] is False
    assert held["button"] == ""


def test_fast_visual_wrapper_preserves_scope_failure_instead_of_hiding_it():
    import json

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    raw = ToolResult(json.dumps({
        "ok": False,
        "verified": False,
        "blocked": True,
        "failure_type": "TARGET_WINDOW_REPLACED",
        "missing_live_target": True,
        "error": "The remembered target HWND is no longer live.",
        "scope_guard": {"before": {"target_hwnd": None}},
    }))

    payload = json.loads(AgentRunner._fast_wrapped_result(
        "click", {"x": 10, "y": 20}, raw,
    ).text)

    assert payload["ok"] is False
    assert payload["blocked"] is True
    assert payload["failure_type"] == "TARGET_WINDOW_REPLACED"
    assert payload["missing_live_target"] is True
    assert "no longer live" in payload["error"]
    assert payload["scope_guard"]["before"]["target_hwnd"] is None


def test_top_level_file_dialog_path_input_is_not_auto_submitted():
    import json

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    path = r"C:\Users\zou\Desktop\WinPilot_Status.docx"
    runner = AgentRunner(f"save the document as {path}")
    runner.session.hwnd = 7
    runner.session.snapshot = Snapshot(
        hwnd=7,
        title="Save As",
        region=Region(0, 0, 1200, 800),
        elements=[
            UIElement(elem_id=1, role="combobox", text="File name:=draft.docx"),
            UIElement(elem_id=2, role="button", text="Save"),
        ],
    )
    calls = []

    def fake_dispatch(name, args):
        calls.append((name, args))
        return ToolResult(json.dumps({
            "ok": True,
            "verified": False,
            "needs_observe": True,
            "last_action": "type_text",
            "results": [
                {"action": "type_text", "ok": True, "data": {"ok": True}},
            ],
        }))

    runner.session.dispatch = fake_dispatch
    out = runner._dispatch_with_fast_visual_wrap("type_text", {"text": path})
    payload = json.loads(out.text)

    assert calls == [("run_action_batch", {
        "actions": [{"action": "type_text", "text": path}],
    })]
    assert payload["source_action"] == "type_text"
    assert payload["verified"] is False


    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    elements = [
        UIElement(elem_id=i, role="listitem", text=f"item_{i}", cx=i, cy=i)
        for i in range(200)
    ]
    elements.append(
        UIElement(
            elem_id=201,
            role="listitem",
            text="WinPilotDragTest_999",
            cx=1852,
            cy=58,
            left=1805,
            top=5,
            width=94,
            height=67,
        )
    )
    snap = Snapshot(hwnd=1, title="Program Manager", region=Region(0, 0, 1920, 1080), elements=elements)

    plain = snap.to_text(max_elements=160)
    filtered = snap.to_text(max_elements=160, query="WinPilotDragTest_999")

    assert "WinPilotDragTest_999" not in plain
    assert 'Filtered elements matching query "winpilotdragtest_999"' in filtered
    assert "WinPilotDragTest_999" in filtered
    assert "center=(1852,58)" in filtered




def test_gui_search_done_uses_generic_fresh_frame_after_opening_result(
    monkeypatch,
    mocked_visual_judge_capability,
):
    from winpilot.agent.loop import AgentRunner

    task = "Open Acme Music, search Blue Echo, then play the first result."
    runner = AgentRunner(task)
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._recent_results = [
        ("click", '{"ok": true, "native_action_executed": true}'),
    ]

    def fresh_playback_screen():
        runner._fresh_done_image_b64 = "fresh-playing-frame"
        return 'Window: "Acme Music"\nNow playing\nPause\n00:07 / 03:42'

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_playback_screen)
    captured = {}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "fresh frame shows active playback"}'
                        ))
                    )])

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True
    assert captured
    user_content = captured["messages"][1]["content"]
    assert isinstance(user_content, list)
    prompt = next(part["text"] for part in user_content if part.get("type") == "text")
    assert task in prompt
    assert "Required visible content" not in prompt
    assert any(part.get("type") == "image_url" for part in user_content)


def test_upload_publish_task_does_not_inject_media_playbook(monkeypatch):
    from winpilot.agent import playbooks

    books = [
        playbooks.Playbook(
            name="media-playback",
            description="",
            content="media",
            keywords=("mp4",),
        ),
        playbooks.Playbook(
            name="file-dialog",
            description="",
            content="file dialog",
            keywords=("upload",),
        ),
    ]
    monkeypatch.setattr(playbooks, "load_all", lambda: books)
    monkeypatch.setattr(playbooks, "enabled", lambda: True)

    matched = playbooks.for_task(
        "upload C:\\Users\\tester\\Desktop\\demo.mp4 through Aurora Studio"
    )

    assert [book.name for book in matched] == ["file-dialog"]


def test_disabled_playbooks_inject_nothing(monkeypatch):
    from winpilot.agent import playbooks

    monkeypatch.setattr(playbooks, "enabled", lambda: False)
    monkeypatch.setattr(
        playbooks,
        "load_all",
        lambda: (_ for _ in ()).throw(AssertionError("disabled playbooks must not load")),
    )

    assert playbooks.for_task("save a file") == []


def test_playbooks_are_opt_in_by_default():
    from winpilot.config import _DEFAULTS

    assert _DEFAULTS["agent"]["playbooks_enabled"] is False


def test_gui_only_detects_bare_chinese_gui_operation_marker():
    from winpilot.agent.task_rules import _is_gui_only_task

    task = "\u5fc5\u987b\u901a\u8fc7GUI\u64cd\u4f5c"

    assert _is_gui_only_task(task)


def test_gui_only_detects_chinese_do_not_use_shell_phrase():
    from winpilot.agent.task_rules import _gui_only_forbids_shell

    assert _gui_only_forbids_shell("只使用真实GUI操作，不要使用shell或文件工具")


def test_saved_text_file_has_local_deterministic_done_evidence(tmp_path):
    from winpilot.agent.loop import AgentRunner

    target = tmp_path / "WinPilot_Office_GUI_20260712.txt"
    runner = AgentRunner(
        f'Use the GUI to save "OFFICE_GUI_712" as {target}'
    )
    target.write_text("OFFICE_GUI_712\n", encoding="utf-8")
    screen = f"{target.name} - Notepad\nOFFICE_GUI_712"

    verdict = runner._deterministic_saved_file_verdict(screen, "")

    assert verdict is not None
    assert verdict[0] is True
    assert "Saved output accepted" in verdict[1]


def test_saved_file_verifier_uses_fresh_native_title_when_snapshot_lags(tmp_path, monkeypatch):
    import win32gui

    from winpilot.agent.loop import AgentRunner

    target = tmp_path / "native-title-lag.txt"
    target.write_text("TITLE_LAG_42\n", encoding="utf-8")
    runner = AgentRunner(f'Save "TITLE_LAG_42" as {target}')
    runner.session.hwnd = 123
    monkeypatch.setattr(win32gui, "GetWindowText", lambda _hwnd: f"{target.name} - Editor")

    verdict = runner._deterministic_saved_file_verdict("old-name.txt - Editor", "")

    assert verdict is not None
    assert verdict[0] is True


def test_explicit_absolute_output_path_does_not_create_prose_basename():
    from pathlib import Path

    from winpilot.agent.done_verifier import _explicit_saved_paths

    task = (
        r"Save as C:\Users\zou\Desktop\Report_R2.docx, then verify that the exact "
        r"file exists and the window title is Report_R2.docx."
    )

    assert _explicit_saved_paths(task) == [
        Path(r"C:\Users\zou\Desktop\Report_R2.docx")
    ]


def _write_minimal_docx(path, paragraphs):
    from zipfile import ZipFile

    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    items = []
    for text, alignment, bold in paragraphs:
        align_xml = f'<w:pPr><w:jc w:val="{alignment}"/></w:pPr>' if alignment else ""
        bold_xml = "<w:rPr><w:b/></w:rPr>" if bold else ""
        items.append(f"<w:p>{align_xml}<w:r>{bold_xml}<w:t>{text}</w:t></w:r></w:p>")
    document = (
        f'<w:document xmlns:w="{namespace}"><w:body>'
        + "".join(items)
        + "</w:body></w:document>"
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document.encode("utf-8"))


def test_saved_docx_done_gate_rejects_wrong_requested_format(tmp_path):
    from winpilot.agent.loop import AgentRunner

    target = tmp_path / "format_bad.docx"
    _write_minimal_docx(target, [
        ("Weekly Report", "center", False),
        ("Body one", "left", False),
        ("Body two", "both", False),
    ])
    runner = AgentRunner(
        f"Save {target}; make the first line bold and centered, and the remaining body left aligned"
    )

    verdict = runner._deterministic_saved_file_verdict(target.name, "")

    assert verdict is not None
    assert verdict[0] is False
    assert "not fully bold" in verdict[1]
    assert "not left aligned" in verdict[1]


def test_saved_docx_done_gate_accepts_requested_format(tmp_path):
    from winpilot.agent.loop import AgentRunner

    target = tmp_path / "format_good.docx"
    _write_minimal_docx(target, [
        ("Weekly Report", "center", True),
        ("Body one", "left", False),
        ("Body two", "", False),
    ])
    runner = AgentRunner(
        f"Save {target}; make the first line bold and centered, and the remaining body left aligned"
    )

    verdict = runner._deterministic_saved_file_verdict(target.name, "")

    assert verdict is not None
    assert verdict[0] is True


    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("\u5728\u753b\u56fe\u753b\u5e03\u7a7a\u767d\u5904\u753b\u4e00\u4e2a\u5c0f\u4e09\u89d2\u5f62")
    runner._done_rejects = 99

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("drawing force-accept should be disabled")

    assert runner._verify_done(_Client()) is False
    assert runner._done_unverified is False
    assert "cannot be force-accepted" in runner._last_done_reject_reason


def test_done_reject_cap_reopens_after_causal_repair_and_fresh_frame(
    monkeypatch,
    mocked_visual_judge_capability,
):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("inspect the repaired target window and report its state")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._done_rejects = 99
    runner._last_done_reject_generation = runner._done_evidence_generation()

    # A successful repair attempt creates a new causal generation. The cap
    # must block repeated unsupported claims, not permanently brick completion.
    runner._tool_history.append(("click", {"element_id": 7}, '{"ok": true}'))

    def fresh_screen():
        runner._fresh_done_image_b64 = "fresh-repaired-frame"
        return "Fresh native-vision frame attached"

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_screen)

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "the repaired state is visible"}'
                        ))
                    )])

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True
    assert runner._done_rejects == 0


def test_repeated_done_claims_do_not_reset_reject_cap():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("inspect the current window and report its state")
    runner._done_rejects = 99
    runner._tool_history = [("done", {}, "first unsupported completion claim")]
    runner._last_done_reject_generation = runner._done_evidence_generation()
    runner._tool_history.append(("done", {}, "same unsupported completion claim"))
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("repeated done must be capped before the judge")
            ))
        )
    )

    assert runner._verify_done(client) is False
    assert "cannot be force-accepted" in runner._last_done_reject_reason
    assert runner._done_reject_terminal is True
    assert runner._safe_stop_after_repeated_done_rejection() is True
    assert runner.status == "fail"
    assert "最终视觉验收" in runner.result_text


def test_failed_plain_text_action_does_not_reset_reject_cap():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("inspect the current window and report its state")
    runner._done_rejects = 99
    runner._tool_history = [("click", {"element_id": 4}, "no current observation; element unavailable")]
    runner._last_done_reject_generation = runner._done_evidence_generation()
    runner._tool_history.append((
        "click", {"element_id": 4}, "no current observation; element unavailable",
    ))

    assert runner._verify_done(client=None) is False
    assert "cannot be force-accepted" in runner._last_done_reject_reason


def test_identical_reobserve_does_not_reset_reject_cap():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("inspect the current window and report its state")
    runner._done_rejects = 99
    runner._last_visual_feedback_b64 = "identical-frame"
    runner._tool_history = [(
        "observe", {},
        "Window: Target\nStatus: unchanged\nPerception trace: run/perception_001_full.json",
    )]
    runner._last_done_reject_generation = runner._done_evidence_generation()
    runner._visual_frame_id += 1
    runner._tool_history.append((
        "observe", {},
        "Window: Target\nStatus: unchanged\nPerception trace: run/perception_002_full.json",
    ))

    assert runner._verify_done(client=None) is False
    assert "cannot be force-accepted" in runner._last_done_reject_reason


def test_done_judge_failure_is_not_treated_as_success():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("inspect the current window and report its state")
    runner.result_text = "done"

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise RuntimeError("provider unavailable")

    assert runner._verify_done(_Client(), steps_left=0) is False
    assert runner._done_verified is False
    assert runner._done_unverified is False
    assert "remains unverified" in runner._last_done_reject_reason


@pytest.mark.parametrize("invalid_done", ["false", "true", 1, 0, None, [], {}])
def test_done_judge_accepts_only_literal_json_true(invalid_done):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("inspect the current window and report its state")
    runner.result_text = "done"

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps({
                            "done": invalid_done,
                            "reason": "provider returned a non-boolean verdict",
                        }))
                    )])

    assert runner._call_done_judge(_Client(), "window", "recent", "evidence") is False
    assert runner._done_verified is False


def test_native_done_judge_prioritizes_fresh_pixels_and_later_successful_input(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent import done_verifier as done_mod

    monkeypatch.setattr(done_mod, "model_supports_vision", lambda _model: True)

    runner = AgentRunner(
        "Click the calibration button with the real GUI mouse and confirm CALIBRATION_OK."
    )
    runner.result_text = "CALIBRATION_OK is visibly present"
    runner._fresh_done_image_b64 = "fresh-frame"
    captured = {}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "fresh pixels show the exact result after native input"}'
                        ))
                    )])

    evidence = (
        '- run_action_batch: {"ok": false, "error": "old malformed attempt"}\n'
        '- click: {"ok": true, "native_action_executed": true, '
        '"source_batch_verified": null}'
    )
    assert runner._call_done_judge(_Client(), "", "run_action_batch; click", evidence) is True

    system_prompt = captured["messages"][0]["content"]
    user_content = captured["messages"][1]["content"]
    user_prompt = next(part["text"] for part in user_content if part.get("type") == "text")
    assert "AUTHORITATIVE NATIVE GUI EVIDENCE ORDER" in user_prompt
    assert "native_action_executed=true" in user_prompt
    assert "earlier attempt failed" in user_prompt
    assert "source_batch_verified is null" in user_prompt
    assert "Do not require a redundant observe/read_text" in user_prompt
    assert "只输出 JSON" in system_prompt


def test_generic_task_never_force_accepts_after_repeated_rejects():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("inspect the current window")
    runner._done_rejects = 99

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("repeated rejects should terminate before the judge")

    assert runner._verify_done(_Client()) is False
    assert runner._done_unverified is False
    assert "cannot be force-accepted" in runner._last_done_reject_reason


def test_spatial_done_uses_original_goal_and_fresh_frame_without_fixed_geometry(
    monkeypatch,
    mocked_visual_judge_capability,
):
    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "\u628a\u684c\u9762\u4e0a\u7684 WinPilotPerfDrag_Eval "
        "\u6587\u4ef6\u5939\u56fe\u6807\u62d6\u52a8\u5230\u684c\u9762\u53f3\u4e0a\u89d2\u9644\u8fd1"
    )
    runner.session.plan = planner.Plan([
        "\u663e\u793a\u684c\u9762",
        "\u5c06 WinPilotPerfDrag_Eval \u6587\u4ef6\u5939\u56fe\u6807\u62d6\u52a8\u5230\u684c\u9762\u53f3\u4e0a\u89d2\u9644\u8fd1",
    ])
    runner.session.plan.complete(1)
    runner.session.plan.complete(2)
    runner._steps_log = ["drag({\"x1\": 1757, \"y1\": 144, \"x2\": 1850, \"y2\": 92})"]
    runner._recent_results = [
        (
            "drag",
            (
                '{"ok": true, "verified": true, "visual_change_detected": true, '
                '"adjusted_drop_target": {"x": 1850, "y": 92, "adjusted": true}}'
            ),
        )
    ]
    def fresh_screen():
        runner._fresh_done_image_b64 = "fresh-frame"
        return 'Window: "Desktop"\n[1] listitem "WinPilot.." center=(1850,92) bbox=(1805,59,94,67)'

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_screen)
    captured = {}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "icon is visibly in the requested corner"}'
                        ))
                    )])

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True
    content = captured["messages"][1]["content"]
    prompt = next(part["text"] for part in content if part.get("type") == "text")
    assert "WinPilotPerfDrag_Eval" in prompt
    assert "AUTHORITATIVE SPATIAL RESULT CHECK" not in prompt


def test_text_only_pro_routes_fresh_gui_frame_to_configured_vision_model(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent import done_verifier as done_mod

    class _Config:
        values = {
            "api": {"model": "mimo-v2.5-pro"},
            "vlm": {
                "enabled": True,
                "base_url": "https://vision.invalid/v1",
                "model": "mimo-v2.5",
            },
            "agent": {},
        }

        def get(self, *keys, default=None):
            value = self.values
            for key in keys:
                if not isinstance(value, dict) or key not in value:
                    return default
                value = value[key]
            return value

    captured = {}

    class _VisionClient:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "fresh frame satisfies the original task"}'
                        ))
                    )])

    class _TextClient:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **_kwargs):
                    raise AssertionError("text-only Pro client must not receive image content")

    monkeypatch.setattr(done_mod, "_config", lambda: _Config())
    monkeypatch.setattr(done_mod.vlm_mod, "_client", lambda: _VisionClient())
    monkeypatch.setattr(
        done_mod.vlm_mod,
        "_extra_body",
        lambda: {"thinking": {"type": "disabled"}},
    )

    task = "create a polished illustration in the visible canvas"
    runner = AgentRunner(task)
    runner.result_text = "done"
    runner._fresh_done_image_b64 = "fresh-dog-frame"

    assert runner._call_done_judge(_TextClient(), "", "draw_strokes", "evidence") is True
    assert captured["model"] == "mimo-v2.5"
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    user_content = captured["messages"][1]["content"]
    assert any(part.get("type") == "image_url" for part in user_content)
    user_prompt = next(part["text"] for part in user_content if part.get("type") == "text")
    assert task in user_prompt
    assert "AUTHORITATIVE NATIVE GUI EVIDENCE ORDER" in user_prompt
    assert "AUTHORITATIVE DRAWING-QUALITY CHECK" not in user_prompt
    assert "at least three canvas colors" not in user_prompt


def test_text_only_pro_verify_done_captures_fresh_drawing_frame_for_vision_judge(monkeypatch):
    import json

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent import done_verifier as done_mod
    from winpilot.utils.image_capture import VisualCaptureScope
    from winpilot.utils.screenshot import Region

    class _Config:
        values = {
            "api": {"model": "mimo-v2.5-pro"},
            "vlm": {
                "enabled": True,
                "base_url": "https://vision.invalid/v1",
                "model": "mimo-v2.5",
            },
            "agent": {},
        }

        def get(self, *keys, default=None):
            value = self.values
            for key in keys:
                if not isinstance(value, dict) or key not in value:
                    return default
                value = value[key]
            return value

    captured = {}
    capture_calls = []

    class _VisionClient:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=(
                            '{"done": true, "reason": "fresh frame shows a complete polished dog"}'
                        ))
                    )])

    class _TextClient:
        class chat:
            class completions:
                @staticmethod
                def create(*_args, **_kwargs):
                    raise AssertionError("text-only Pro client must not receive image content")

    monkeypatch.setattr(done_mod, "_config", lambda: _Config())
    monkeypatch.setattr(
        done_mod,
        "select_visual_capture_scope",
        lambda *_args, **_kwargs: VisualCaptureScope(
            Region(10, 20, 800, 600),
            "target_window",
            "verified_foreground_target",
            target_hwnd=321,
            target_verified=True,
        ),
    )
    monkeypatch.setattr(
        done_mod,
        "capture_window_jpeg_b64",
        lambda hwnd, **_kwargs: capture_calls.append(hwnd) or "fresh-dog-frame",
    )
    monkeypatch.setattr(done_mod.vlm_mod, "_client", lambda: _VisionClient())
    monkeypatch.setattr(
        done_mod.vlm_mod,
        "_extra_body",
        lambda: {"thinking": {"type": "disabled"}},
    )

    runner = AgentRunner("draw a good-looking dog in Paint")
    runner.session.hwnd = 321
    runner.result_text = "done"
    runner._steps_log = ['execute_subgoal({"draws": [...]})']
    draw_result = json.dumps({
        "ok": True,
        "verified": True,
        "visual_change_detected": True,
        "actions_executed": [{"action": "draw_strokes"}],
        "evidence_packet": {
            "matched_visual_colors": [],
            "missing_visual_colors": [],
        },
    })
    runner._recent_results = [("execute_subgoal", draw_result)]
    runner._tool_history = [
        ("execute_subgoal", {"draws": [{"shape": "ellipse", "part": "body"}]}, draw_result),
    ]

    assert runner._verify_done(_TextClient()) is True
    assert capture_calls == [321]
    assert runner._fresh_done_image_b64 == "fresh-dog-frame"
    assert captured["model"] == "mimo-v2.5"
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert any(
        part.get("type") == "image_url"
        for part in captured["messages"][1]["content"]
    )

# Drawing completion is covered by the generic fresh-frame contracts above.
