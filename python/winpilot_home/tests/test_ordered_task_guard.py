import json


TASK = "用计算器计算88和76的商,然后打开qq给我的手机发一张计算器结果的截图"


def test_qq_is_blocked_until_calculator_result_is_grounded():
    from winpilot.agent import scope_guard
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task=TASK)

    blocked = scope_guard.ensure_scope(session, "launch", {"command": "QQ"})

    assert blocked is not None
    payload = json.loads(blocked.text)
    assert payload["blocked"] is True
    assert "Calculator result" in payload["error"]


def test_qq_is_blocked_until_verified_calculator_window_is_saved(tmp_path):
    from winpilot.agent import scope_guard
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task=TASK)
    session.visible_evidence_ledger = [{
        "source": "execute_subgoal:evidence_packet",
        "target": "1.157894736842105",
        "snippet": "Calculator display is 1.157894736842105",
    }]

    blocked = scope_guard.ensure_scope(session, "launch", {"command": "QQ"})

    assert blocked is not None
    assert "screenshot" in json.loads(blocked.text)["error"]

    screenshot = tmp_path / "calculator.png"
    screenshot.write_bytes(b"png")
    session.saved_screenshot_paths = [str(screenshot)]
    assert scope_guard.ensure_scope(session, "launch", {"command": "QQ"}) is None
