from winpilot.agent.loop import AgentRunner
from winpilot.agent.router import build_runner


def test_gui_only_task_stays_on_unified_desktop_agent():
    runner = build_runner(
        "GUI-only test. Open Notepad and save C:\\Users\\zou\\Desktop\\x.txt through GUI."
    )

    assert isinstance(runner, AgentRunner)
    assert not runner.runtime_profile.coding
    assert runner.runtime_profile.desktop
    assert "desktop" in runner.runtime_profile.capabilities
    assert "coding" not in runner.runtime_profile.capabilities
    assert "desktop_action_queue" in runner.runtime_profile.capabilities


def test_coding_task_uses_unified_agent_with_coding_runtime():
    runner = build_runner("调试这个 python 项目的报错并运行测试")

    assert isinstance(runner, AgentRunner)
    assert runner.runtime_profile.coding
    assert runner.runtime_profile.to_dict()["capabilities"] == [
        "desktop",
        "coding",
        "replay",
        "scheduler",
        "automation_ir",
        "desktop_action_queue",
    ]


def test_runner_and_tool_session_share_runtime_profile():
    runner = build_runner("Debug this Python project and run tests")

    assert runner.session.runtime_profile is runner.runtime_profile


def test_runner_can_disable_shell_tools_for_arp_policy():
    runner = build_runner("Open Calculator and send a screenshot in QQ", allow_shell_tools=False)

    assert runner.allow_shell_tools is False


def test_explicit_project_artifact_task_uses_coding_runtime():
    runner = build_runner("这是一个 Python 项目编程任务：创建 main.py 并确认 stdout 是 7")

    assert isinstance(runner, AgentRunner)
    assert runner.runtime_profile.coding
