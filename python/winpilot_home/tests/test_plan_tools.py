from types import SimpleNamespace


def test_revise_plan_drops_unrequested_drawing_save_step():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="\u5728\u753b\u677f\u4e0a\u753b\u4e00\u53ea\u5f88\u7ec6\u8282\u7684\u732b")
    session._t_revise_plan({"steps": [
        "draw the cat",
        "save the drawing as a PNG",
        "verify the visible details",
    ]})

    goals = [str(step.get("goal") or "") for step in session.plan.steps]
    assert goals == ["draw the cat", "verify the visible details"]


def test_revise_plan_keeps_requested_drawing_save_step():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="draw a cat and save it as cat.png")
    session._t_revise_plan({"steps": ["draw the cat", "save it as cat.png"]})

    goals = [str(step.get("goal") or "") for step in session.plan.steps]
    assert goals == ["draw the cat", "save it as cat.png"]


def test_planner_grounds_invented_public_desktop_path_to_current_user():
    from pathlib import Path

    from winpilot.agent.planner import _ground_plan_file_checks

    steps = [{
        "goal": "保存到桌面",
        "check": {"file_exists": r"C:\Users\Public\Desktop\meeting.txt"},
    }]

    grounded = _ground_plan_file_checks(
        "保存到桌面，文件名为 meeting.txt",
        steps,
    )

    assert grounded[0]["check"]["file_exists"] == str(
        Path.home() / "Desktop" / "meeting.txt"
    )


def test_planner_preserves_long_explicit_output_path_and_adds_file_check():
    import json

    from winpilot.agent.planner import _ground_plan_file_checks, _parse_steps

    path = r"C:\Users\zou\Desktop\WinPilot_WPS_Status_GUI_R2_20260712.docx"
    raw = json.dumps([{"goal": f"通过GUI另存为 {path}"}], ensure_ascii=False)
    grounded = _ground_plan_file_checks(
        f"通过GUI把文档另存为 {path}",
        _parse_steps(raw),
    )

    assert path in grounded[0]["goal"]
    assert grounded[0]["check"] == {"file_exists": path}


def test_planner_simplifies_multi_paragraph_input_check_to_stable_first_paragraph():
    from winpilot.agent.planner import _sanitize_check_for_goal

    check = _sanitize_check_for_goal(
        "输入四段指定文本，每段一行",
        {
            "text_appears": (
                "办公自动化验收报告 测试标识：OFFICE_GUI_712 "
                "已完成：输入、格式化与状态核验 结论：真实文件保存成功"
            )
        },
    )

    assert check == {"text_appears": "办公自动化验收报告"}


def _false_verify():
    return SimpleNamespace(
        check=lambda *_args, **_kwargs: SimpleNamespace(
            verified=False,
            detail="missing current text",
        )
    )


def test_complete_subgoal_accepts_historical_visible_evidence(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(tools_mod, "verify", _false_verify())
    session = ToolSession(task="确认 LOCAL_COUNTER 曾出现后继续")
    session.hwnd = 123
    session.plan = planner.Plan([
        {
            "goal": "确认 LOCAL_COUNTER 曾出现",
            "check": {"text_appears": "LOCAL_COUNTER"},
        }
    ])
    session.visible_evidence_ledger = [
        {
            "source": "observe",
            "target": "LOCAL_COUNTER",
            "snippet": "LOCAL_COUNTER 2",
        }
    ]

    session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is True


def test_complete_subgoal_rejects_historical_evidence_for_current_task(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(tools_mod, "verify", _false_verify())
    session = ToolSession(task="确认 LOCAL_COUNTER 当前可见")
    session.hwnd = 123
    session.plan = planner.Plan([
        {
            "goal": "确认 LOCAL_COUNTER 当前可见",
            "check": {"text_appears": "LOCAL_COUNTER"},
        }
    ])
    session.visible_evidence_ledger = [
        {
            "source": "observe",
            "target": "LOCAL_COUNTER",
            "snippet": "LOCAL_COUNTER 2",
        }
    ]

    session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is False


def test_complete_subgoal_accepts_current_execute_subgoal_evidence(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(tools_mod, "verify", _false_verify())
    session = ToolSession(task="确认 LOCAL_COUNTER 当前可见")
    session.hwnd = 123
    session.plan = planner.Plan([
        {
            "goal": "确认 LOCAL_COUNTER 当前可见",
            "check": {"text_appears": "LOCAL_COUNTER"},
        }
    ])
    session.visible_evidence_ledger = [
        {
            "source": "execute_subgoal:evidence_packet",
            "target": "LOCAL_COUNTER",
            "snippet": "subgoal matched visible text: LOCAL_COUNTER",
        }
    ]

    session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is True


def test_complete_subgoal_accepts_current_visual_color_evidence(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(tools_mod, "verify", _false_verify())
    session = ToolSession(task="画一个蓝色三角形")
    session.hwnd = 123
    session.plan = planner.Plan([
        {
            "goal": "在画布上绘制一个蓝色三角形",
            "check": {"text_appears": "蓝色"},
        }
    ])
    # Simulate older/in-flight plans created before drawing color checks were
    # sanitized out by the planner.
    session.plan.steps[0]["check"] = {"text_appears": "蓝色"}
    session.visible_evidence_ledger = [
        {
            "source": "execute_subgoal:visual_color",
            "target": "blue",
            "snippet": "subgoal matched visual color: blue",
        }
    ]

    session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is True


def test_complete_subgoal_all_done_returns_terminal_done():
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="complete the visible GUI task")
    session.plan = planner.Plan(["finish the only step"])

    result = session._t_complete_subgoal({"index": 1})

    assert session.plan.all_done()
    assert result.terminal == "done"


def test_complete_subgoal_is_idempotent_after_controller_completion(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(tools_mod, "verify", _false_verify())
    session = ToolSession(task="drag a desktop folder icon to the top right")
    session.hwnd = 123
    session.plan = planner.Plan([
        {
            "goal": "drag the desktop folder icon to the top right",
            "check": {"file_exists": r"C:\Users\Public\Desktop\WinPilotPerfDrag_Eval"},
        }
    ])
    session.plan.complete(1)

    result = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is True
    assert result.terminal == "done"


def test_complete_subgoal_does_not_treat_app_launch_as_new_blank_creation(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(
        tools_mod,
        "verify",
        SimpleNamespace(
            check=lambda *_args, **_kwargs: SimpleNamespace(
                verified=True,
                detail="WPS window is visible",
            )
        ),
    )
    session = ToolSession(task="open WPS Spreadsheets and create a new blank workbook")
    session.hwnd = 123
    session.plan = planner.Plan([{
        "goal": "open WPS Spreadsheets and create a new blank workbook",
        "check": {"text_appears": "WPS Spreadsheets"},
    }])

    rejected = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is False
    assert "does not prove" in rejected.text

    session._new_blank_applied = True
    accepted = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is True
    assert accepted.terminal == "done"


def test_complete_research_source_step_requires_matching_working_set_subject():
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    session = ToolSession(
        task="Inspect two separate product pages for MX Master 3S and MX Anywhere 3S"
    )
    session.plan = planner.Plan([
        {"goal": "Collect name, battery, weight, and URL from MX Anywhere 3S"},
    ])
    session.world.record_facts(
        "MX Master 3S",
        {"weight": "not visible"},
        source_url="https://example.test/mx-master-3s",
    )

    rejected = session._t_complete_subgoal({"index": 1})

    assert "no source record matching" in rejected.text
    assert session.plan.steps[0]["done"] is False

    session.world.record_facts(
        "MX Anywhere 3S",
        {"weight": "not visible"},
        source_url="https://example.test/mx-anywhere-3s",
    )
    accepted = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is True
    assert accepted.terminal == "done"


def test_revise_plan_restores_source_progress_from_working_set():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(
        task="Inspect two separate product pages for MX Master 3S and MX Anywhere 3S"
    )
    session.world.record_facts(
        "MX Master 3S",
        {"weight": "not visible"},
        source_url="https://example.test/mx-master-3s",
    )
    session.world.record_facts(
        "MX Anywhere 3S",
        {"weight": "not visible"},
        source_url="https://example.test/mx-anywhere-3s",
    )

    result = session._t_revise_plan({"steps": [
        "Open the MX Master 3S official product page",
        "Collect fields from MX Master 3S",
        "Open the MX Anywhere 3S official product page",
        "Collect fields from MX Anywhere 3S",
        "Create a WPS Writer document",
        "Save the WPS Writer document",
    ]})

    assert [step["done"] for step in session.plan.steps] == [True, True, True, True, False, False]
    assert "[1, 2, 3, 4]" in result.text


def test_complete_subgoal_rejects_unchecked_question_step():
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="complete this quiz in the visible app")
    session.plan = planner.Plan(["Answer question 2"])

    result = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is False
    assert result.terminal is None
    assert "explicit current evidence" in result.text


def test_complete_subgoal_accepts_read_text_from_current_screen_epoch(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(tools_mod, "verify", _false_verify())
    session = ToolSession(task="confirm OFFICE_GUI_712 is currently visible")
    session.hwnd = 123
    session._screen_epoch = 7
    session.plan = planner.Plan([{
        "goal": "confirm OFFICE_GUI_712 is currently visible",
        "check": {"text_appears": "OFFICE_GUI_712"},
    }])
    session.visible_evidence_ledger = [{
        "source": "read_text",
        "target": "OFFICE_GUI_712",
        "snippet": "测试标识：OFFICE_GUI_712",
        "screen_epoch": 7,
    }]

    session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is True


def test_complete_subgoal_rejects_read_text_after_screen_epoch_changes(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(tools_mod, "verify", _false_verify())
    session = ToolSession(task="confirm OFFICE_GUI_712 is currently visible")
    session.hwnd = 123
    session._screen_epoch = 8
    session.plan = planner.Plan([{
        "goal": "confirm OFFICE_GUI_712 is currently visible",
        "check": {"text_appears": "OFFICE_GUI_712"},
    }])
    session.visible_evidence_ledger = [{
        "source": "read_text",
        "target": "OFFICE_GUI_712",
        "snippet": "测试标识：OFFICE_GUI_712",
        "screen_epoch": 7,
    }]

    session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is False


def test_complete_subgoal_rejects_unchecked_submit_when_completion_below_100():
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="complete the visible form")
    session.plan = planner.Plan(["Submit the form"])
    session.visible_evidence_ledger = [
        {
            "source": "execute_subgoal:evidence_packet",
            "target": "completion rate 60%",
            "snippet": "completion rate 60%",
        }
    ]

    result = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is False
    assert result.terminal is None
    assert "completion_rate=60%" in result.text


def test_complete_subgoal_rejects_pending_unverified_action_without_check():
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="complete the visible GUI task")
    session.plan = planner.Plan(["finish the current UI state"])
    session.run_state = SimpleNamespace(unverified_actions=2)

    result = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is False
    assert result.terminal is None
    assert "2 GUI action" in result.text


def test_complete_subgoal_ignores_invented_drawing_file_check_without_requested_path(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(tools_mod, "verify", _false_verify())
    session = ToolSession(
        task="draw a blue triangle and a red rectangle, then confirm them on screen"
    )
    session.hwnd = 123
    session.plan = planner.Plan([
        {
            "goal": "screenshot or save the canvas to confirm the shapes",
            "check": {"file_exists": r"C:\Users\Public\Pictures\paint_confirm.png"},
        }
    ])

    result = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is True
    assert result.terminal == "done"


def test_complete_subgoal_keeps_requested_drawing_file_check(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(tools_mod, "verify", _false_verify())
    requested_path = r"C:\Users\Public\Pictures\paint_confirm.png"
    session = ToolSession(
        task=f"draw a blue triangle and save the final image as {requested_path}"
    )
    session.hwnd = 123
    session.plan = planner.Plan([
        {
            "goal": "save the final drawing file",
            "check": {"file_exists": requested_path},
        }
    ])

    result = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is False
    assert result.terminal is None


def test_complete_subgoal_ignores_winpilot_schedule_windows_task_file_check(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(tools_mod, "verify", _false_verify())
    session = ToolSession(
        task=(
            "Create a WinPilot scheduled task named Agent Scheduler E2E Smoke "
            "to run one hour later"
        )
    )
    session.hwnd = None
    session.plan = planner.Plan([
        {
            "goal": "create WinPilot scheduled task Agent Scheduler E2E Smoke",
            "check": {
                "file_contains": {
                    "path": r"C:\Windows\System32\Tasks\Agent Scheduler E2E Smoke",
                    "text": "Agent Scheduler E2E Smoke",
                }
            },
        }
    ])

    result = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is True
    assert result.terminal == "done"

