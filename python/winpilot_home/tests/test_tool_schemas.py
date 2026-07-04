def _names(tools):
    return [tool["function"]["name"] for tool in tools]


def test_execute_subgoal_is_prioritized_before_low_level_gui_mutations():
    from winpilot.agent.tool_schemas import tool_schemas

    names = _names(tool_schemas(planning=True, coding_runtime=False, allow_shell=False))

    assert names.index("execute_subgoal") < names.index("click")
    assert names.index("execute_subgoal") < names.index("type_text")


def test_execute_subgoal_schema_warns_verify_only_needs_target_window():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(planning=True, coding_runtime=False, allow_shell=False)
    execute = next(tool for tool in tools if tool["function"]["name"] == "execute_subgoal")

    assert "Verify-only execute_subgoal requires an existing target window" in execute["function"]["description"]
    assert "subgoal alone is only a label and is invalid" in execute["function"]["description"]
    assert "not executable by itself" in execute["function"]["parameters"]["properties"]["subgoal"]["description"]


def test_execute_subgoal_schema_requires_concrete_routine_guidance():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(planning=True, coding_runtime=False, allow_shell=False)
    execute = next(tool for tool in tools if tool["function"]["name"] == "execute_subgoal")
    description = execute["function"]["description"]

    assert "input, click/clicks, draw/draws, or verify_texts" in description


def test_schema_exposes_agent_schedules_without_shell():
    from winpilot.agent.tool_schemas import tool_schemas

    names = set(_names(tool_schemas(planning=True, coding_runtime=False, allow_shell=False)))

    assert {
        "schedule_task",
        "list_scheduled_tasks",
        "cancel_scheduled_task",
    } <= names


def test_schema_exposes_coordinate_calibration_tools_without_shell():
    from winpilot.agent.tool_schemas import tool_schemas

    names = set(_names(tool_schemas(planning=True, coding_runtime=False, allow_shell=False)))

    assert {"coordinate_diagnostics", "coordinate_click_test"} <= names


def test_strict_gui_schema_hides_file_tools_without_hiding_gui_tools():
    from winpilot.agent.tool_schemas import tool_schemas

    names = set(_names(tool_schemas(planning=True, coding_runtime=False, allow_shell=False, allow_files=False)))

    assert {"search_files", "list_dir", "read_file", "write_file", "search_text"}.isdisjoint(names)
    assert {"execute_subgoal", "run_action_batch", "launch", "screenshot", "done"} <= names


def test_coding_runtime_keeps_file_tools_even_when_gui_files_are_hidden():
    from winpilot.agent.tool_schemas import tool_schemas

    names = set(_names(tool_schemas(planning=True, coding_runtime=True, allow_shell=False, allow_files=False)))

    assert {"list_dir", "read_file", "write_file", "search_text", "apply_patch", "github_search"} <= names


def test_lean_gui_profile_keeps_controller_tools_and_hides_unrelated_tools():
    from winpilot.agent.tool_schemas import tool_schemas

    standard = tool_schemas(planning=True, coding_runtime=False, allow_shell=False, allow_files=False)
    lean = tool_schemas(
        planning=True,
        coding_runtime=False,
        allow_shell=False,
        allow_files=False,
        profile="lean_gui",
    )
    names = set(_names(lean))

    assert len(lean) < len(standard)
    assert {
        "list_windows",
        "launch",
        "focus_window",
        "observe",
        "execute_subgoal",
        "run_action_batch",
        "click",
        "type_text",
        "draw_strokes",
        "screenshot",
        "coordinate_diagnostics",
        "coordinate_click_test",
        "done",
        "fail",
        "revise_plan",
        "complete_subgoal",
    } <= names
    assert {
        "capture_template",
        "find_image",
        "color_probe",
        "clipboard_read",
        "mouse_down",
        "mouse_up",
        "move_mouse",
        "list_installed_apps",
    }.isdisjoint(names)


def test_schema_profile_auto_uses_lean_only_for_gui_drawing_tasks(monkeypatch):
    from winpilot.agent.loop import _schema_profile_for_task

    monkeypatch.setattr(
        "winpilot.agent.loop.CONFIG.get",
        lambda *path, default=None: "auto" if path == ("agent", "tool_profile") else default,
    )

    assert _schema_profile_for_task("请只用GUI打开画图画一个蓝色三角形", is_coding=False) == "drawing_gui"
    assert _schema_profile_for_task("打开QQ给我的手机发消息", is_coding=False) == "standard"
    assert _schema_profile_for_task("重构这个 Python 项目", is_coding=True) == "standard"


def test_drawing_gui_profile_keeps_draw_subgoal_without_click_input():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(
        planning=True,
        coding_runtime=False,
        allow_shell=False,
        allow_files=False,
        profile="drawing_gui",
    )
    execute = next(tool for tool in tools if tool["function"]["name"] == "execute_subgoal")
    props = set(execute["function"]["parameters"]["properties"])

    assert {"subgoal", "target_window", "draw", "draws", "verify_texts", "constraints"} <= props
    assert {"input", "click", "clicks"}.isdisjoint(props)
    assert "Do not use it to open/discover an app" in execute["function"]["description"]
