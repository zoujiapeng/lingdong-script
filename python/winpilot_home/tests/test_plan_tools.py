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

