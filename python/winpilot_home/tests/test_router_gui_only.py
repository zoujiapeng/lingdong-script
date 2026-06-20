from winpilot.agent.loop import AgentRunner
from winpilot.agent.router import build_runner


def test_gui_only_task_stays_on_desktop_agent():
    runner = build_runner(
        "GUI-only test. Open Notepad and save C:\\Users\\zou\\Desktop\\x.txt through GUI."
    )

    assert isinstance(runner, AgentRunner)
