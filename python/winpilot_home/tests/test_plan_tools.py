from types import SimpleNamespace


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


def test_complete_subgoal_rejects_unchecked_question_step():
    from winpilot.agent import planner
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="complete this quiz in the visible app")
    session.plan = planner.Plan(["Answer question 2"])

    result = session._t_complete_subgoal({"index": 1})

    assert session.plan.steps[0]["done"] is False
    assert result.terminal is None
    assert "explicit current evidence" in result.text


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

