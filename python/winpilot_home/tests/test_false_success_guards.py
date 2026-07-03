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


def test_gui_only_shell_policy():
    from winpilot.agent.loop import _gui_only_shell_allowed, _is_gui_only_task

    assert _is_gui_only_task("GUI-only test. Use Notepad.")
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
