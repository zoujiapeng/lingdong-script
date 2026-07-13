def _names(tools):
    return [tool["function"]["name"] for tool in tools]


def test_execute_subgoal_is_prioritized_before_low_level_gui_mutations():
    from winpilot.agent.tool_schemas import tool_schemas

    names = _names(tool_schemas(planning=True, coding_runtime=False, allow_shell=False))

    assert names.index("execute_subgoal") < names.index("click")
    assert names.index("execute_subgoal") < names.index("type_text")


def test_open_app_is_prioritized_before_manual_app_discovery_loop():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(planning=True, coding_runtime=False, allow_shell=False)
    names = _names(tools)
    open_app = next(tool for tool in tools if tool["function"]["name"] == "open_app")

    assert names.index("open_app") < names.index("find_app")
    assert open_app["function"]["parameters"]["required"] == ["name"]


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


def test_execute_subgoal_schema_exposes_normalized_detailed_drawing_primitives():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(
        planning=True,
        coding_runtime=False,
        allow_shell=False,
        profile="drawing_gui",
    )
    execute = next(tool for tool in tools if tool["function"]["name"] == "execute_subgoal")
    draw = execute["function"]["parameters"]["properties"]["draw"]
    draws = execute["function"]["parameters"]["properties"]["draws"]["items"]
    description = execute["function"]["description"]

    assert {"box", "box_mode", "part", "normalized_strokes"} <= set(draw["properties"])
    assert {"box", "box_mode", "part", "normalized_strokes"} <= set(draws["properties"])
    assert draw["properties"]["box"]["maxItems"] == 4
    assert "normalized_strokes=[[[x,y],...]]]" in description
    assert "6-16 waypoints" in description
    assert "never use rectangles for natural limbs" in description


def test_schema_exposes_agent_schedules_without_shell():
    from winpilot.agent.tool_schemas import tool_schemas

    names = set(_names(tool_schemas(planning=True, coding_runtime=False, allow_shell=False)))

    assert {
        "schedule_task",
        "list_scheduled_tasks",
        "cancel_scheduled_task",
    } <= names


def test_schema_exposes_pluggable_web_search_without_shell():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(planning=True, coding_runtime=False, allow_shell=False)
    web_search = next(tool for tool in tools if tool["function"]["name"] == "web_search")

    assert web_search["function"]["parameters"]["required"] == ["query"]
    assert web_search["function"]["parameters"]["properties"]["max_results"]["type"] == "integer"


def test_schema_exposes_coordinate_calibration_tools_without_shell():
    from winpilot.agent.tool_schemas import tool_schemas

    names = set(_names(tool_schemas(planning=True, coding_runtime=False, allow_shell=False)))

    assert {"coordinate_diagnostics", "coordinate_click_test"} <= names


def test_schema_exposes_per_run_source_bound_working_set():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(planning=True, coding_runtime=False, allow_shell=False)
    record = next(tool for tool in tools if tool["function"]["name"] == "record_facts")

    assert record["function"]["parameters"]["required"] == ["subject", "facts"]
    assert "not long-term memory" in record["function"]["description"]


def test_multi_source_research_task_detection_requires_research_and_multiple_sources():
    from winpilot.agent.task_rules import _is_multi_source_research_task

    assert _is_multi_source_research_task(
        "Search two separate product pages and copy each source URL into a report"
    )
    assert _is_multi_source_research_task("调研两款商品的独立官方页面并分别记录来源")
    assert not _is_multi_source_research_task("Open one product page")


def test_search_files_schema_exposes_fast_local_filters():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(
        planning=True,
        coding_runtime=False,
        allow_shell=False,
        allow_files=True,
    )
    search = next(tool for tool in tools if tool["function"]["name"] == "search_files")
    properties = search["function"]["parameters"]["properties"]

    assert properties["kind"]["enum"] == ["any", "file", "folder"]
    assert properties["sort"]["enum"] == ["path", "name", "date_modified", "size"]
    assert properties["extensions"]["items"] == {"type": "string"}


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
        "open_app",
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
    assert {"find_app", "find_program", "tray_click"}.isdisjoint(names)
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


def test_communication_task_uses_lean_gui_profile():
    from winpilot.agent.gui_plan_helpers import _schema_profile_for_task

    assert _schema_profile_for_task(
        '打开QQ给我的手机发送"HELLO"并确认消息出现在聊天记录中',
        is_coding=False,
    ) == "lean_gui"


def test_schema_profile_auto_uses_lean_for_gui_and_communication_tasks(monkeypatch):
    from winpilot.agent.loop import _schema_profile_for_task

    monkeypatch.setattr(
        "winpilot.agent.loop.CONFIG.get",
        lambda *path, default=None: "auto" if path == ("agent", "tool_profile") else default,
    )

    assert _schema_profile_for_task("请只用GUI打开画图画一个蓝色三角形", is_coding=False) == "drawing_gui"
    assert _schema_profile_for_task("打开QQ给我的手机发消息", is_coding=False) == "lean_gui"
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
    names = set(_names(tools))
    execute = next(tool for tool in tools if tool["function"]["name"] == "execute_subgoal")
    props = set(execute["function"]["parameters"]["properties"])

    assert {"revise_plan", "complete_subgoal"}.isdisjoint(names)
    assert {"execute_subgoal", "done", "blocked"} <= names
    assert {"subgoal", "target_window", "draw", "draws", "verify_texts", "constraints"} <= props
    assert {"input", "click", "clicks"}.isdisjoint(props)
    assert "Do not use it to open/discover an app" in execute["function"]["description"]


def test_native_vision_hides_redundant_secondary_vlm_tools(monkeypatch):
    from winpilot.agent import tool_schemas as schemas

    monkeypatch.setattr(schemas.vlm, "is_configured", lambda: True)
    text_only = set(_names(schemas.tool_schemas(native_vision=False, allow_shell=False)))
    native = set(_names(schemas.tool_schemas(native_vision=True, allow_shell=False)))

    assert {"vlm_describe", "vlm_verify"} <= text_only
    assert {"vlm_describe", "vlm_verify"}.isdisjoint(native)


def test_native_vision_exposes_only_normalized_frame_coordinates():
    from winpilot.agent import tool_schemas as schemas

    def by_name(tools, name):
        return next(tool for tool in tools if tool["function"]["name"] == name)

    for profile in ("standard", "lean_gui"):
        native = schemas.tool_schemas(
            native_vision=True,
            allow_shell=False,
            allow_files=False,
            profile=profile,
        )
        names = set(_names(native))
        assert "coordinate_click_test" not in names
        assert "blocked" in names

        click = by_name(native, "click")["function"]["parameters"]
        assert {"frame_x", "frame_y"} <= set(click["properties"])
        assert {"x", "y"}.isdisjoint(click["properties"])

        scroll = by_name(native, "scroll")["function"]["parameters"]
        assert {"frame_x", "frame_y", "amount"} <= set(scroll["required"])
        assert {"x", "y"}.isdisjoint(scroll["properties"])

        drag = by_name(native, "drag")["function"]["parameters"]
        assert {"frame_x1", "frame_y1", "frame_x2", "frame_y2"} <= set(drag["required"])
        assert {"x1", "y1", "x2", "y2", "points"}.isdisjoint(drag["properties"])
        assert "frame_points" in drag["properties"]

        draw = by_name(native, "draw_strokes")["function"]["parameters"]
        assert "frame_strokes" in draw["required"]
        assert "strokes" not in draw["properties"]

        batch = by_name(native, "run_action_batch")["function"]
        batch_item = batch["parameters"]["properties"]["actions"]["items"]
        batch_text = (
            batch["description"]
            + " "
            + batch_item["description"]
        )
        assert batch_item["required"] == ["action"]
        assert "click" in batch_item["properties"]["action"]["enum"]
        assert "one spatial action" in batch_text
        assert "direct click/scroll/drag/draw_strokes" in batch_text
        assert "frame_x/frame_y" in batch_text
        assert "frame_strokes" in batch_text
        assert "Never send physical" in batch_text
        assert "at most one spatial mutation" in batch_text
        assert "element_id" in batch_text
        assert "next real post-action frame" in batch_text

    text_only = schemas.tool_schemas(
        native_vision=False,
        allow_shell=False,
        allow_files=False,
    )
    click = by_name(text_only, "click")["function"]["parameters"]
    assert {"x", "y"} <= set(click["properties"])
    assert {"frame_x", "frame_y"}.isdisjoint(click["properties"])
    assert "coordinate_click_test" in set(_names(text_only))
