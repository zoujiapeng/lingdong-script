from types import SimpleNamespace


def test_drawing_task_does_not_trigger_on_screen_stability_phrase():
    from winpilot.agent.task_rules import _is_drawing_task

    assert not _is_drawing_task("点击按钮后等待约1秒让画面稳定，再观察最近变化区域")
    assert _is_drawing_task("画一个三角形")
    assert _is_drawing_task("draw a triangle on the canvas")


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


def test_required_literal_tokens_extracts_distinctive_payloads_not_urls():
    from winpilot.agent.task_rules import _required_literal_tokens

    assert _required_literal_tokens(
        "Open http://127.0.0.1:8878/gui_agent_target.html, type FAST_WRAP_OK_071, "
        "then confirm LOCAL_COUNTER and result 75765."
    ) == ["FAST_WRAP_OK_071", "LOCAL_COUNTER", "75765"]


def test_done_rejects_missing_distinctive_literal_even_when_other_state_changed(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Open the target page, type FAST_WRAP_OK_071 into the input, then confirm LOCAL_COUNTER."
    )
    runner.session.hwnd = 123
    runner.result_text = "done; clicked the local counter instead"
    runner._recent_results = [
        ("click", '{"ok": true, "verified": true, "verify_detail": "LOCAL_COUNTER appeared"}'),
    ]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Target"\n[1] text "LOCAL_COUNTER_1"'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("missing exact literal should reject before LLM judge")

    assert runner._verify_done(_Client()) is False
    assert "FAST_WRAP_OK_071" in runner._last_done_reject_reason


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

    runner = AgentRunner("confirm APPLIED_TEXT_1 and LOCAL_COUNTER_4")
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


def test_auto_done_accepts_strong_targets_from_visible_tool_evidence():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner(
        "confirm APPLIED_TEXT_1, E2E_TARGET_AUTO_101 and LOCAL_COUNTER_4 are visible"
    )
    result = ToolResult(
        "Window: Target\nAPPLIED_TEXT_1: E2E_TARGET_AUTO_101\nLOCAL_COUNTER_4"
    )
    before = len(runner._visible_evidence_ledger)

    runner._track_visible_evidence("read_text", {}, result.text)
    out = runner._maybe_auto_done_from_visible_evidence("read_text", result, before)

    assert out.terminal == "done"
    assert '"auto_done": true' in out.text


def test_auto_done_ignores_verified_expect_without_visible_tool_evidence():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("confirm APPLIED_TEXT_1 and LOCAL_COUNTER_4 are visible")
    before = len(runner._visible_evidence_ledger)
    runner._append_visible_evidence(
        "launch:verified_expect",
        "APPLIED_TEXT_1",
        "verified expect text appeared: APPLIED_TEXT_1",
    )
    runner._append_visible_evidence(
        "launch:verified_expect",
        "LOCAL_COUNTER_4",
        "verified expect text appeared: LOCAL_COUNTER_4",
    )

    out = runner._maybe_auto_done_from_visible_evidence(
        "launch",
        ToolResult("{}"),
        before,
    )

    assert out.terminal is None


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


def test_text_appears_can_use_fullscreen_without_target(monkeypatch):
    from winpilot.action import verify

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


def test_text_appears_uses_indexed_uia_query_before_fusion(monkeypatch):
    from winpilot.action import verify

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
    from winpilot.agent.task_rules import _gui_only_blocked_hotkey

    assert _is_gui_only_task("GUI-only test. Use Notepad.")
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
    assert _gui_only_blocked_hotkey("GUI only, complete it visibly.", ["ctrl", "shift", "j"])
    assert _gui_only_blocked_hotkey("GUI only, complete it visibly.", ["ctrl", "r"])
    assert not _gui_only_blocked_hotkey("GUI only, refresh the page visibly.", ["ctrl", "r"])
    assert not _gui_only_blocked_hotkey("GUI only, open developer tools.", ["ctrl", "shift", "j"])


def test_gui_only_dispatch_blocks_browser_reset_and_devtools_hotkeys(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession

    calls = []
    fake_executor = SimpleNamespace(
        press_keys=lambda keys: calls.append(keys) or {"ok": True},
        focus_window=lambda hwnd: {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)

    session = ToolSession(task="Use GUI only to operate the visible web app.")

    devtools = session.dispatch("hotkey", {"keys": ["ctrl", "shift", "j"]})
    refresh = session.dispatch("hotkey", {"keys": ["ctrl", "r"]})

    assert "developer-tools hotkey" in devtools.text
    assert "refresh/reload hotkey" in refresh.text
    assert calls == []


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


def test_save_task_refuses_cancel_button_click():
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="Save this file as C:\\Users\\zou\\Desktop\\x.txt")
    session.snapshot = Snapshot(
        hwnd=1,
        title="Save As",
        region=Region(0, 0, 800, 600),
        elements=[UIElement(elem_id=7, role="button", text="Cancel", cx=10, cy=10, source="uia")],
    )

    result = session._t_click({"element_id": 7})

    assert "Refused to click" in result.text


def test_non_power_task_refuses_power_button_click():
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="把桌面上的文件夹拖到右上角")
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

    assert "Refused to click a system power" in result.text


def test_coordinate_click_refuses_power_element_under_point():
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="打开浏览器访问网页")
    session.snapshot = Snapshot(
        hwnd=1,
        title="DesktopWindowXamlSource",
        region=Region(0, 0, 1920, 1080),
        elements=[
            UIElement(
                elem_id=9,
                role="button",
                text="关机",
                cx=100,
                cy=100,
                left=80,
                top=80,
                width=50,
                height=40,
                source="uia",
            )
        ],
    )

    result = session._t_click({"x": 100, "y": 100})

    assert "Refused to click a system power" in result.text


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
    fake_executor = SimpleNamespace(
        uia_invoke=lambda element: calls.append("uia") or True,
        click=lambda *args, **kwargs: calls.append("mouse") or {"ok": True},
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
    result = session._t_type_text({"text": "C:\\Users\\zou\\Desktop\\x.txt"})

    assert calls == [(3, "C:\\Users\\zou\\Desktop\\x.txt")]
    assert "uia_set_value" in result.text


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


def test_simple_drawing_done_uses_deterministic_visual_evidence(monkeypatch):
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

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("deterministic drawing done should not run fresh observe")
        ),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run with deterministic drawing evidence")

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True


def test_simple_drawing_done_accepts_batch_draw_strokes(monkeypatch):
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

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("deterministic batch drawing done should not run fresh observe")
        ),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run with batch drawing evidence")

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True


def test_simple_drawing_done_accepts_compacted_late_batch_visual_evidence(monkeypatch):
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

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("deterministic compacted drawing done should not run fresh observe")
        ),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run with compacted batch evidence")

    assert "visual_change_detected" in compact
    assert len(compact) < 900
    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True


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
    assert "combine obvious adjacent" in payload["guidance"]
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


def test_execute_subgoal_draw_expands_adjacent_plan_draws():
    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("打开画图，画蓝色三角形和红色矩形")
    runner.session.plan = planner.Plan([
        "打开画图",
        "在左边绘制蓝色三角形",
        "在右边绘制红色矩形",
        "确认画布上同时存在蓝色三角形和红色矩形",
    ])
    runner.session.plan.complete(1)
    args = {
        "subgoal": "Draw a blue triangle",
        "draw": {"shape": "triangle", "color": "blue", "expected_region": "left"},
    }

    expanded = runner._maybe_expand_execute_subgoal_draws_from_plan("execute_subgoal", args)

    assert expanded is not args
    assert expanded["draws"] == [
        {"shape": "triangle", "color": "blue", "expected_region": "left"},
        {"shape": "rectangle", "color": "red", "expected_region": "right"},
    ]
    assert "combined adjacent drawing plan items" in expanded["subgoal"]


def test_execute_subgoal_draw_expands_from_task_when_plan_has_verify_noise():
    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "open Paint, draw a blue triangle on the left and a red rectangle on the right"
    )
    runner.session.plan = planner.Plan([
        "open Paint and verify blue triangle",
        "draw red rectangle on the right",
        "confirm both shapes are visible",
    ])
    args = {
        "subgoal": "Draw a blue triangle",
        "draw": {"shape": "triangle", "color": "blue", "expected_region": "left"},
    }

    expanded = runner._maybe_expand_execute_subgoal_draws_from_plan("execute_subgoal", args)

    assert expanded is not args
    assert expanded["draws"] == [
        {"shape": "triangle", "color": "blue", "expected_region": "left"},
        {"shape": "rectangle", "color": "red", "expected_region": "right"},
    ]


def test_execute_subgoal_draw_expands_from_task_without_plan():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "draw a blue triangle on the left and a red rectangle on the right"
    )
    args = {
        "subgoal": "Draw a blue triangle",
        "draw": {"shape": "triangle", "color": "blue", "expected_region": "left"},
    }

    expanded = runner._maybe_expand_execute_subgoal_draws_from_plan("execute_subgoal", args)

    assert expanded is not args
    assert expanded["draws"] == [
        {"shape": "triangle", "color": "blue", "expected_region": "left"},
        {"shape": "rectangle", "color": "red", "expected_region": "right"},
    ]


def test_verified_launch_auto_completes_pure_open_plan_item():
    import json

    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("open Paint, then draw a blue triangle")
    runner.session.plan = planner.Plan([
        "open Windows Paint app",
        "draw a blue triangle",
    ])
    result = ToolResult(json.dumps({
        "ok": True,
        "verified": True,
        "target_hwnd": 123,
    }))

    updated = runner._maybe_auto_complete_open_plan_item("launch", {"command": "mspaint"}, result)
    payload = json.loads(updated.text)

    assert runner.session.plan.steps[0]["done"] is True
    assert runner.session.plan.steps[1]["done"] is False
    assert payload["auto_completed_plan_items"][0]["index"] == 1
    assert "next unfinished item" in payload["fast_path_advice"]


def test_verified_launch_does_not_auto_complete_mixed_open_draw_goal():
    import json

    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("open Paint and draw a blue triangle")
    runner.session.plan = planner.Plan([
        "open Paint and verify blue triangle",
        "draw a red rectangle",
    ])
    result = ToolResult(json.dumps({
        "ok": True,
        "verified": True,
        "target_hwnd": 123,
    }))

    updated = runner._maybe_auto_complete_open_plan_item("launch", {"command": "mspaint"}, result)

    assert updated is result
    assert runner.session.plan.steps[0]["done"] is False


def test_execute_subgoal_draw_does_not_expand_custom_strokes():
    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw custom strokes")
    runner.session.plan = planner.Plan([
        "绘制蓝色三角形",
        "绘制红色矩形",
    ])
    args = {
        "subgoal": "Draw exact custom stroke",
        "draw": {
            "shape": "triangle",
            "color": "blue",
            "strokes": [[[1, 2], [3, 4]]],
        },
    }

    assert runner._maybe_expand_execute_subgoal_draws_from_plan("execute_subgoal", args) is args


def test_execute_subgoal_draw_only_expands_adjacent_plan_items():
    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw shapes with a verification break")
    runner.session.plan = planner.Plan([
        "open paint",
        "draw blue triangle on the left",
        "verify the blue triangle is visible",
        "draw red rectangle on the right",
    ])
    runner.session.plan.complete(1)
    args = {
        "subgoal": "Draw a blue triangle",
        "draw": {"shape": "triangle", "color": "blue", "expected_region": "left"},
    }

    assert runner._maybe_expand_execute_subgoal_draws_from_plan("execute_subgoal", args) is args


def test_execute_subgoal_draw_does_not_expand_before_current_plan_item():
    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw shapes in order")
    runner.session.plan = planner.Plan([
        "draw blue triangle on the left",
        "draw red rectangle on the right",
    ])
    args = {
        "subgoal": "Draw a red rectangle",
        "draw": {"shape": "rectangle", "color": "red", "expected_region": "right"},
    }

    assert runner._maybe_expand_execute_subgoal_draws_from_plan("execute_subgoal", args) is args


def test_execute_subgoal_draw_reuses_recent_controller_evidence():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a blue triangle and a red rectangle on the canvas")
    runner._tool_history = [(
        "execute_subgoal",
        {
            "subgoal": "Draw requested shapes",
            "draws": [
                {"shape": "triangle", "color": "blue", "expected_region": "left"},
                {"shape": "rectangle", "color": "red", "expected_region": "right"},
            ],
        },
        json.dumps({
            "ok": True,
            "verified": True,
            "actions_executed": [
                {"action": "draw_strokes", "shape": "triangle", "requested_color": "blue"},
                {"action": "draw_strokes", "shape": "rectangle", "requested_color": "red"},
            ],
            "evidence_packet": {
                "matched_visual_colors": ["blue", "red"],
                "missing_visual_colors": [],
            },
        }),
    )]

    result = runner._maybe_reuse_recent_draw_evidence(
        "execute_subgoal",
        {
            "subgoal": "Draw a red rectangle on the right side",
            "draw": {"shape": "rectangle", "color": "red", "expected_region": "right"},
        },
    )

    assert result is not None
    payload = json.loads(result.text)
    assert payload["subgoal_route"] == "controller_evidence_reuse"
    assert payload["actions_executed"] == []
    assert payload["evidence_packet"]["matched_visual_colors"] == ["red"]


def test_execute_subgoal_draw_reuse_is_conservative():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a blue triangle and a red rectangle on the canvas")
    runner._tool_history = [(
        "execute_subgoal",
        {"draw": {"shape": "rectangle", "color": "red", "expected_region": "left"}},
        json.dumps({
            "ok": True,
            "verified": True,
            "actions_executed": [
                {"action": "draw_strokes", "shape": "rectangle", "requested_color": "red"},
            ],
            "evidence_packet": {
                "matched_visual_colors": ["red"],
                "missing_visual_colors": [],
            },
        }),
    )]

    assert runner._maybe_reuse_recent_draw_evidence(
        "execute_subgoal",
        {"draw": {"shape": "rectangle", "color": "red", "expected_region": "right"}},
    ) is None
    assert runner._maybe_reuse_recent_draw_evidence(
        "execute_subgoal",
        {"draw": {"shape": "rectangle", "color": "red", "strokes": [[[1, 2], [3, 4]]]}},
    ) is None
    assert runner._maybe_reuse_recent_draw_evidence(
        "execute_subgoal",
        {"draw": {"shape": "triangle", "color": "red", "expected_region": "left"}},
    ) is None


def test_execute_subgoal_color_verify_reuses_recent_visual_evidence():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a blue triangle and a red rectangle")
    runner._recent_results = [(
        "execute_subgoal",
        json.dumps({
            "verified": True,
            "evidence_packet": {
                "matched_visual_colors": ["blue", "red"],
                "missing_visual_colors": [],
            },
        }),
    )]

    result = runner._maybe_reuse_recent_visual_color_verify(
        "execute_subgoal",
        {"subgoal": "verify the blue and red shapes", "verify_texts": ["blue", "red"]},
    )

    assert result is not None
    payload = json.loads(result.text)
    assert payload["subgoal_route"] == "controller_visual_evidence_reuse"
    assert payload["evidence_packet"]["matched_visual_colors"] == ["blue", "red"]


def test_execute_subgoal_color_verify_reuse_requires_all_colors_and_drawing_task():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a blue triangle and a red rectangle")
    runner._recent_results = [(
        "execute_subgoal",
        json.dumps({
            "verified": True,
            "evidence_packet": {"matched_visual_colors": ["blue"]},
        }),
    )]

    assert runner._maybe_reuse_recent_visual_color_verify(
        "execute_subgoal",
        {"verify_texts": ["blue", "red"]},
    ) is None

    non_drawing = AgentRunner("type blue and red into a text field")
    non_drawing._recent_results = runner._recent_results
    assert non_drawing._maybe_reuse_recent_visual_color_verify(
        "execute_subgoal",
        {"verify_texts": ["blue"]},
    ) is None


def test_execute_subgoal_draws_auto_complete_matching_plan_items():
    import json

    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("draw a blue triangle and a red rectangle")
    runner.session.plan = planner.Plan([
        "draw a blue triangle",
        "draw a red rectangle on the right",
        "verify both shapes are visible",
    ])
    args = {
        "draws": [
            {"shape": "triangle", "color": "blue", "expected_region": "left"},
            {"shape": "rectangle", "color": "red", "expected_region": "right"},
        ],
    }
    result = ToolResult(json.dumps({
        "ok": True,
        "verified": True,
        "actions_executed": [
            {"action": "draw_strokes", "shape": "triangle", "requested_color": "blue"},
            {"action": "draw_strokes", "shape": "rectangle", "requested_color": "red"},
        ],
        "evidence_packet": {
            "matched_visual_colors": ["blue", "red"],
            "missing_visual_colors": [],
        },
    }))

    updated = runner._maybe_auto_complete_draw_plan_items("execute_subgoal", args, result)
    payload = json.loads(updated.text)

    assert [step["done"] for step in runner.session.plan.steps] == [True, True, True]
    assert [item["index"] for item in payload["auto_completed_plan_items"]] == [1, 2, 3]
    assert payload["plan_all_done"] is True
    assert updated.terminal == "done"
    assert "plan_after_auto_complete" in payload


def test_execute_subgoal_draws_does_not_auto_complete_file_output_task():
    import json

    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    output_path = r"C:\Users\Public\Pictures\paint_confirm.png"
    runner = AgentRunner(f"draw a blue triangle and red rectangle, then save as {output_path}")
    runner.session.plan = planner.Plan([
        "draw a blue triangle",
        "draw a red rectangle on the right",
        "verify both shapes are visible",
        {"goal": "save the final drawing file", "check": {"file_exists": output_path}},
    ])
    args = {
        "draws": [
            {"shape": "triangle", "color": "blue", "expected_region": "left"},
            {"shape": "rectangle", "color": "red", "expected_region": "right"},
        ],
    }
    result = ToolResult(json.dumps({
        "ok": True,
        "verified": True,
        "actions_executed": [
            {"action": "draw_strokes", "shape": "triangle", "requested_color": "blue"},
            {"action": "draw_strokes", "shape": "rectangle", "requested_color": "red"},
        ],
        "evidence_packet": {
            "matched_visual_colors": ["blue", "red"],
            "missing_visual_colors": [],
        },
    }))

    updated = runner._maybe_auto_complete_draw_plan_items("execute_subgoal", args, result)
    payload = json.loads(updated.text)

    assert [step["done"] for step in runner.session.plan.steps] == [True, True, False, False]
    assert [item["index"] for item in payload["auto_completed_plan_items"]] == [1, 2]
    assert "plan_all_done" not in payload
    assert updated.terminal is None


def test_execute_subgoal_draws_auto_complete_requires_matched_color():
    import json

    from winpilot.agent import planner
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("draw a blue triangle and a red rectangle")
    runner.session.plan = planner.Plan([
        "draw a blue triangle",
        "draw a red rectangle on the right",
    ])
    args = {
        "draws": [
            {"shape": "triangle", "color": "blue", "expected_region": "left"},
            {"shape": "rectangle", "color": "red", "expected_region": "right"},
        ],
    }
    result = ToolResult(json.dumps({
        "ok": True,
        "verified": False,
        "actions_executed": [
            {"action": "draw_strokes", "shape": "triangle", "requested_color": "blue"},
            {"action": "draw_strokes", "shape": "rectangle", "requested_color": "red"},
        ],
        "evidence_packet": {
            "matched_visual_colors": ["blue"],
            "missing_visual_colors": ["red"],
        },
    }))

    updated = runner._maybe_auto_complete_draw_plan_items("execute_subgoal", args, result)

    assert updated is result
    assert [step["done"] for step in runner.session.plan.steps] == [False, False]


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


def test_plan_draw_parser_ignores_visual_verification_items():
    from winpilot.agent.loop import _parse_plan_draw_spec

    assert _parse_plan_draw_spec("确认画布上同时存在蓝色三角形和红色矩形") == {}
    assert _parse_plan_draw_spec("在画布上绘制蓝色三角形") == {
        "shape": "triangle",
        "color": "blue",
    }
    assert _parse_plan_draw_spec("在蓝色三角形右边绘制一个红色矩形") == {
        "shape": "rectangle",
        "color": "red",
        "expected_region": "right",
    }


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


def test_done_gate_rejects_recent_missing_visual_color():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a blue triangle on the canvas")
    runner._recent_results = [
        ("execute_subgoal", json.dumps({
            "actions_executed": [{"action": "draw_strokes"}],
            "evidence_packet": {
                "missing_visual_colors": ["blue"],
                "matched_visual_colors": [],
            },
            "results": [{"action": "draw_strokes", "data": {"visual_change_detected": True}}],
        })),
    ]

    assert runner._verify_drawing_done(json.dumps({
        "visual_change_detected": True,
        "missing_visual_colors": ["blue"],
    })) is False
    assert "still missing: blue" in runner._last_done_reject_reason


def test_drawing_done_requires_matched_visual_color_even_when_steps_are_low():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a blue triangle on the canvas")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['execute_subgoal({"draw": {"shape": "triangle", "color": "blue"}})']
    runner._recent_results = [
        ("execute_subgoal", json.dumps({
            "actions_executed": [{"action": "draw_strokes"}],
            "evidence_packet": {
                "matched_visual_colors": [],
                "missing_visual_colors": [],
            },
            "results": [{"action": "draw_strokes", "data": {"visual_change_detected": True}}],
        })),
    ]

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("missing visual color evidence should reject before LLM judge")

    assert runner._verify_done(_Client(), steps_left=0) is False
    assert "has not matched them yet: blue" in runner._last_done_reject_reason


def test_drawing_done_accepts_controller_visual_color_before_step_budget_force_accept():
    import json

    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a blue triangle on the canvas")
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['execute_subgoal({"draw": {"shape": "triangle", "color": "blue"}})']
    runner._recent_results = [
        ("execute_subgoal", json.dumps({
            "actions_executed": [{"action": "draw_strokes"}],
            "evidence_packet": {
                "matched_visual_colors": ["blue"],
                "missing_visual_colors": [],
            },
            "results": [{"action": "draw_strokes", "data": {"visual_change_detected": True}}],
        })),
    ]

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("controller visual color evidence should skip LLM judge")

    assert runner._verify_done(_Client(), steps_left=0) is True
    assert runner._done_verified is True
    assert runner._done_unverified is False


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


def test_top_level_semantic_click_reports_anchor_source_action():
    import json

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("click export")

    def fake_dispatch(name, args):
        assert name == "run_action_batch"
        return ToolResult(json.dumps({
            "ok": True,
            "verified": True,
            "needs_observe": False,
            "last_action": "click_element",
            "metrics": {"action_count": 1},
            "results": [
                {
                    "index": 1,
                    "action": "click_element",
                    "upgraded_from": "click",
                    "ok": True,
                    "verified": True,
                    "data": {"ok": True, "via": "click_element", "verified": True},
                },
            ],
        }))

    runner.session.dispatch = fake_dispatch
    out = runner._dispatch_with_fast_visual_wrap("click", {"label": "Export", "role": "button"})
    payload = json.loads(out.text)

    assert payload["source_action"] == "click_element"
    assert payload["source_action_verified"] is True
    assert payload["verified"] is True


def test_desktop_icon_drag_done_uses_final_uia_position(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "把桌面上的 WinPilotDragTest_123 文件夹图标通过鼠标拖动到桌面右上角附近。"
    )
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['drag({"x1": 100, "y1": 500, "x2": 1850, "y2": 80})']
    runner._recent_results = [
        ("drag", '{"ok": true, "visual_change_detected": true}'),
    ]

    screen = (
        'Window: "Program Manager" hwnd=1 rect=(0,0,1920x1080)\n'
        '[160] listitem "WinPilotDragTest_123" center=(1852,58) '
        'bbox=(1805,5,94,107) checked,selected,focused src=uia'
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: screen),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run with deterministic drag evidence")

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True


def test_desktop_icon_drag_done_accepts_batch_drag(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "把桌面上的 WinPilotDragTest_123 文件夹图标通过鼠标拖动到桌面右上角附近。"
    )
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = [
        'run_action_batch({"actions": [{"action": "drag", "x1": 100, "y1": 500, "x2": 1850, "y2": 80}]})'
    ]
    runner._recent_results = [
        ("run_action_batch", '{"ok": true, "verified": true, "results": [{"action": "drag", "preview": "{\\"visual_change_detected\\": true}"}]}'),
    ]

    screen = (
        'Window: "Program Manager" hwnd=1 rect=(0,0,1920x1080)\n'
        '[160] listitem "WinPilotDragTest_123" center=(1852,58) '
        'bbox=(1805,5,94,107) checked,selected,focused src=uia'
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: screen),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run with batch drag evidence")

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True


def test_desktop_drag_verified_batch_auto_completes_plan_item():
    import json

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.planner import Plan
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner(
        "把桌面上的 WinPilotDragTest_123 文件夹图标通过鼠标拖动到桌面右上角附近。"
    )
    runner.session.plan = Plan([
        {"goal": "显示桌面"},
        {"goal": "将 WinPilotDragTest_123 文件夹图标拖动到桌面右上角附近"},
    ])
    runner.session.plan.complete(1)
    payload = {
        "ok": True,
        "verified": True,
        "controller_state": {
            "phase": "verified",
            "verified": True,
            "last_action": "drag",
        },
        "results": [
            {
                "action": "drag",
                "data": {
                    "ok": True,
                    "verified": True,
                    "visual_change_detected": True,
                },
            }
        ],
    }

    result = runner._maybe_auto_complete_desktop_drag_plan_item(
        "run_action_batch",
        {"actions": [{"action": "drag", "x1": 100, "y1": 500, "x2": 1850, "y2": 80}]},
        ToolResult(json.dumps(payload)),
    )
    data = json.loads(result.text)

    assert result.terminal == "done"
    assert runner.session.plan.all_done()
    assert data["plan_all_done"] is True
    assert data["auto_completed_plan_items"][0]["index"] == 2


def test_desktop_icon_drag_done_rejects_wrong_area(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "把桌面上的 WinPilotDragTest_123 文件夹图标通过鼠标拖动到桌面右上角附近。"
    )
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._steps_log = ['drag({"x1": 100, "y1": 500, "x2": 960, "y2": 540})']
    runner._recent_results = [
        ("drag", '{"ok": true, "visual_change_detected": true}'),
    ]

    screen = (
        'Window: "Program Manager" hwnd=1 rect=(0,0,1920x1080)\n'
        '[160] listitem "WinPilotDragTest_123" center=(960,540) '
        'bbox=(913,507,94,67) checked src=uia'
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: screen),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content='{"done": false, "reason": "wrong area"}'))]
                    )

    assert runner._verify_done(_Client()) is False


def test_snapshot_to_text_query_surfaces_omitted_target():
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


def test_drawing_stops_mutation_after_plan_done_and_file_exists(tmp_path):
    from winpilot.agent.loop import AgentRunner

    out = tmp_path / "done.png"
    out.write_bytes(b"not-really-a-png-but-existing-is-enough-for-the-guard")
    runner = AgentRunner(f"draw a house on a canvas and save as {out}")
    runner._steps_log = ['drag({"x1": 1, "y1": 1, "x2": 20, "y2": 20})']
    runner.session.plan = SimpleNamespace(all_done=lambda: True)

    assert runner._drawing_should_stop_mutating()


def test_gui_search_done_requires_on_screen_query(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Open Steam client and search Stardew Valley in the store page."
    )
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._recent_results = [
        ("run_shell", "Steam library manifests show Stardew Valley is not installed"),
    ]

    monkeypatch.setattr(
        "winpilot.agent.loop.fusion.observe",
        lambda *a, **k: SimpleNamespace(to_text=lambda n=50: 'Window: "Desktop"\nNo Steam window'),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("LLM judge should not run after fast rejection")

    assert runner._verify_done(_Client()) is False
    assert runner._done_rejects == 1


def test_store_search_allows_store_deeplink_but_blocks_run_protocol():
    from winpilot.agent.loop import _blocks_store_search_action

    task = "Open Steam client and search Stardew Valley in the store page."

    assert _blocks_store_search_action(
        task, "launch", {"command": "steam://rungameid/413150"}
    )
    assert _blocks_store_search_action(
        task, "run_shell", {"command": "Start-Process steam://install/413150"}
    )
    assert not _blocks_store_search_action(
        task, "launch", {"command": "steam://store/413150"}
    )
    assert not _blocks_store_search_action(
        task, "run_shell", {"command": "Start-Process https://store.steampowered.com/app/413150/"}
    )


def test_upload_publish_blocks_media_playback_or_analysis_shell():
    from winpilot.agent.loop import _blocks_upload_media_analysis, _is_upload_publish_task

    task = "打开抖音，上传 C:\\Users\\zou\\Desktop\\demo.mp4，填写标题，但不要最终发布"

    assert _is_upload_publish_task(task)
    assert _is_upload_publish_task(
        "Use creator page with C:\\Users\\zou\\Desktop\\winpilot_douyin_test.mp4"
    )

    assert _blocks_upload_media_analysis(
        task, "run_shell", {"command": "ffprobe C:\\Users\\zou\\Desktop\\demo.mp4"}
    )
    assert _blocks_upload_media_analysis(
        task, "run_shell", {"command": "Start-Process 'C:\\Users\\zou\\Desktop\\demo.mp4'"}
    )
    assert _blocks_upload_media_analysis(
        task,
        "run_detached",
        {"script": "Start-Process wmplayer.exe -ArgumentList 'C:\\Users\\zou\\Desktop\\demo.mp4'"},
    )
    assert _blocks_upload_media_analysis(
        task,
        "launch",
        {"command": '"C:\\Program Files\\Windows Media Player\\wmplayer.exe" "C:\\Users\\zou\\Desktop\\demo.mp4"'},
    )
    assert _blocks_upload_media_analysis(
        task,
        "launch",
        {"command": '"C:\\Program Files\\VideoLAN\\VLC\\vlc.exe" "C:\\Users\\zou\\Desktop\\demo.mp4"'},
    )
    assert _blocks_upload_media_analysis(
        task,
        "launch",
        {"command": "C:\\Users\\zou\\Desktop\\demo.mp4"},
    )
    assert _blocks_upload_media_analysis(
        task,
        "find_program",
        {"name": "Windows Media Player"},
    )
    assert _blocks_upload_media_analysis(
        task,
        "find_program",
        {"name": "VLC media player"},
    )
    assert _blocks_upload_media_analysis(
        task,
        "run_shell",
        {"command": "Get-Command wmplayer.exe"},
    )
    assert not _blocks_upload_media_analysis(
        task, "run_shell", {"command": "Test-Path 'C:\\Users\\zou\\Desktop\\demo.mp4'"}
    )
    assert not _blocks_upload_media_analysis(
        task, "launch", {"command": "C:\\Program Files (x86)\\ByteDance\\douyin\\douyin.exe"}
    )


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

    matched = playbooks.for_task("upload C:\\Users\\zou\\Desktop\\demo.mp4 to Douyin")

    assert [book.name for book in matched] == ["file-dialog"]


def test_gui_only_detects_bare_chinese_gui_operation_marker():
    from winpilot.agent.task_rules import _gui_only_blocked_hotkey, _is_gui_only_task

    task = "\u5fc5\u987b\u901a\u8fc7GUI\u64cd\u4f5c"

    assert _is_gui_only_task(task)
    assert _gui_only_blocked_hotkey(task, ["ctrl", "r"])


def test_desktop_drag_task_does_not_force_accept_after_rejects():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "\u628a\u684c\u9762\u4e0a\u7684 WinPilotPerfDrag_Eval "
        "\u6587\u4ef6\u5939\u56fe\u6807\u62d6\u52a8\u5230\u684c\u9762\u53f3\u4e0a\u89d2\u9644\u8fd1"
    )
    runner._done_rejects = 99

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("max-reject side-effect tasks should not call the judge")

    assert runner._verify_done(_Client()) is False
    assert runner._done_unverified is False
    assert "unverified force-accept is disabled" in runner._last_done_reject_reason


def test_drawing_task_does_not_force_accept_after_rejects():
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
    assert "unverified force-accept is disabled" in runner._last_done_reject_reason


def test_desktop_drag_done_accepts_controller_plan_evidence_without_full_ocr_name(monkeypatch):
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
    monkeypatch.setattr(
        runner,
        "_fresh_done_screen",
        lambda: 'Window: "Desktop"\n[1] listitem "WinPilot.." center=(1850,92) bbox=(1805,59,94,67)',
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(*_a, **_k):
                    raise AssertionError("deterministic desktop drag evidence should skip judge")

    assert runner._verify_done(_Client()) is True
    assert runner._done_verified is True
