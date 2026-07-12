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
        "browser",
        "safety",
        "async_jobs",
        "perception_daemon",
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


def test_report_file_artifact_task_uses_coding_runtime():
    runner = build_runner(
        "\u627e\u5b8c\u6211\u7535\u8111\u4e0a\u7684\u6240\u6709\u8f6f\u4ef6\uff0c"
        "\u5168\u90e8\u5199\u5230\u4e00\u4e2atxt\u6587\u4ef6\u91cc\uff0c\u5e76\u8bb0\u5f55\u8017\u65f6"
    )

    assert runner.runtime_profile.coding
    assert runner.session.runtime_profile is runner.runtime_profile


def test_webpage_build_then_browser_task_uses_mixed_runtime():
    runner = build_runner("\u7f16\u5199\u8bd5\u5377\u7f51\u9875\uff0c\u7136\u540e\u81ea\u5df1\u64cd\u63a7\u6d4f\u89c8\u5668\u7b54\u9898")

    assert runner.runtime_profile.coding
    assert runner.runtime_profile.desktop
    assert runner.runtime_profile.browser


def test_coding_plan_progress_is_not_blocked_by_gui_action_counter():
    runner = build_runner("write a report.txt file and verify its contents")
    runner.session.run_state.unverified_actions = 3

    assert runner.session._unchecked_subgoal_blocker("collect deterministic inputs") == ""


def test_desktop_plan_progress_still_requires_pending_action_evidence():
    runner = build_runner("open Calculator and click the equals button")
    runner.session.run_state.unverified_actions = 2

    blocker = runner.session._unchecked_subgoal_blocker("click equals")

    assert "2 GUI action(s)" in blocker
