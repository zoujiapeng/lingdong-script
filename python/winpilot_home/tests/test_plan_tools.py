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

