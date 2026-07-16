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


def test_execute_subgoal_schema_keeps_generic_spatial_creation_primitives():
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
    assert {"box", "box_mode", "part", "normalized_strokes"} <= set(draw["properties"])
    assert {"box", "box_mode", "part", "normalized_strokes"} <= set(draws["properties"])
    assert draw["properties"]["box"]["maxItems"] == 4
    description = execute["function"]["description"].casefold()
    assert "dog" not in description
    assert "creature" not in description
    assert "15-30" not in description


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


def test_raw_press_primitives_are_exposed_only_inside_validated_controller_batch():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(
        planning=True,
        coding_runtime=False,
        allow_shell=False,
        allow_files=False,
        profile="standard",
    )
    names = set(_names(tools))
    assert {"mouse_down", "mouse_up", "hold_key"}.isdisjoint(names)

    batch = next(
        tool["function"] for tool in tools
        if tool["function"]["name"] == "run_action_batch"
    )
    variants = {
        variant["properties"]["action"]["const"]
        for variant in batch["parameters"]["properties"]["actions"]["items"]["oneOf"]
    }
    assert {"mouse_down", "mouse_up", "hold_key"} <= variants


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

    assert _schema_profile_for_task("请只用GUI打开画图画一个蓝色三角形", is_coding=False) == "lean_gui"
    assert _schema_profile_for_task("打开QQ给我的手机发消息", is_coding=False) == "lean_gui"
    assert _schema_profile_for_task("重构这个 Python 项目", is_coding=True) == "standard"


def test_typed_stage_schema_profile_does_not_reclassify_generated_goal(monkeypatch):
    from winpilot.agent import gui_plan_helpers

    monkeypatch.setattr(
        gui_plan_helpers.CONFIG,
        "get",
        lambda *path, default=None: "auto" if path == ("agent", "tool_profile") else default,
    )

    def unexpected_raw_classifier(_task):
        raise AssertionError("typed stage must not invoke raw task classifiers")

    monkeypatch.setattr(gui_plan_helpers, "_is_gui_only_task", unexpected_raw_classifier)
    monkeypatch.setattr(gui_plan_helpers, "_is_communication_task", unexpected_raw_classifier)

    assert gui_plan_helpers._schema_profile_for_task(
        "generated semantic child goal with misleading repository words",
        is_coding=False,
        native_vision=True,
        task_intent={"operation": ["draw"]},
        stage_type="DrawArtifact",
    ) == "lean_gui"


def test_native_vision_auto_profile_uses_lean_for_generic_gui_operations(monkeypatch):
    from winpilot.agent.gui_plan_helpers import _schema_profile_for_task

    monkeypatch.setattr(
        "winpilot.agent.gui_plan_helpers.CONFIG.get",
        lambda *path, default=None: "auto" if path == ("agent", "tool_profile") else default,
    )

    # Exact acceptance prompt: classification comes from the visible Paint
    # surface/action, not from a special dog or benchmark recipe.
    dog_prompt = "\u7528\u753b\u677f\u753b\u4e00\u53ea\u72d7,\u8981\u597d\u770b\u4e00\u70b9\u4e0d\u8981\u7b80\u964b\u7684"
    assert _schema_profile_for_task(
        dog_prompt,
        is_coding=False,
        native_vision=True,
    ) == "lean_gui"
    assert _schema_profile_for_task(
        "Open Notepad, type the meeting title, and click Save.",
        is_coding=False,
        native_vision=True,
    ) == "lean_gui"


def test_native_vision_auto_profile_does_not_prune_non_gui_or_coding_tasks(monkeypatch):
    from winpilot.agent.gui_plan_helpers import _schema_profile_for_task

    monkeypatch.setattr(
        "winpilot.agent.gui_plan_helpers.CONFIG.get",
        lambda *path, default=None: "auto" if path == ("agent", "tool_profile") else default,
    )

    assert _schema_profile_for_task(
        "Search the API documentation and compare the two authentication methods.",
        is_coding=False,
        native_vision=True,
    ) == "standard"
    assert _schema_profile_for_task(
        "\u68c0\u67e5\u7cfb\u7edf\u65e5\u5fd7\u5e76\u5206\u6790\u5f02\u5e38\u539f\u56e0",
        is_coding=False,
        native_vision=True,
    ) == "standard"
    assert _schema_profile_for_task(
        "Refactor this Python repository and run its tests.",
        is_coding=True,
        native_vision=True,
    ) == "standard"
    # Text-only GUI execution uses the richer lean controller/VLM subset, but
    # still must not pay for unrelated research/system/file tools every turn.
    assert _schema_profile_for_task(
        "Open Notepad, type hello, and click Save.",
        is_coding=False,
        native_vision=False,
    ) == "lean_gui"


def test_text_only_gui_auto_profile_keeps_controller_and_vlm_without_unrelated_tools(monkeypatch):
    from winpilot.agent.gui_plan_helpers import _schema_profile_for_task
    from winpilot.agent.tool_schemas import tool_schemas

    monkeypatch.setattr(
        "winpilot.agent.gui_plan_helpers.CONFIG.get",
        lambda *path, default=None: "auto" if path == ("agent", "tool_profile") else default,
    )
    monkeypatch.setattr("winpilot.agent.tool_schemas.vlm.is_configured", lambda: True)
    profile = _schema_profile_for_task(
        "Open Paint and draw a landscape on the canvas.",
        is_coding=False,
        native_vision=False,
    )
    names = {
        tool["function"]["name"]
        for tool in tool_schemas(
            planning=True,
            coding_runtime=False,
            allow_shell=True,
            allow_files=True,
            profile=profile,
            native_vision=False,
        )
    }

    assert profile == "lean_gui"
    assert {"observe", "execute_subgoal", "vlm_describe", "vlm_verify"} <= names
    assert {"web_search", "schedule_task", "read_file", "write_file"}.isdisjoint(names)


def test_explicit_standard_and_full_profiles_override_native_gui_auto(monkeypatch):
    from winpilot.agent.gui_plan_helpers import _schema_profile_for_task

    task = "Open Notepad, type hello, and click Save."
    for configured in ("standard", "full"):
        monkeypatch.setattr(
            "winpilot.agent.gui_plan_helpers.CONFIG.get",
            lambda *path, default=None, value=configured: (
                value if path == ("agent", "tool_profile") else default
            ),
        )
        assert _schema_profile_for_task(
            task,
            is_coding=False,
            native_vision=True,
        ) == configured


def test_native_gui_auto_profile_reduces_model_schema_to_18_relevant_tools(monkeypatch):
    from winpilot.agent.gui_plan_helpers import _schema_profile_for_task
    from winpilot.agent.tool_schemas import tool_schemas

    monkeypatch.setattr(
        "winpilot.agent.gui_plan_helpers.CONFIG.get",
        lambda *path, default=None: "auto" if path == ("agent", "tool_profile") else default,
    )
    profile = _schema_profile_for_task(
        "Open Notepad, type hello, and click Save.",
        is_coding=False,
        native_vision=True,
    )
    tools = tool_schemas(
        planning=True,
        coding_runtime=False,
        allow_shell=False,
        allow_files=False,
        profile=profile,
        native_vision=True,
    )
    names = set(_names(tools))

    assert len(tools) == 18
    assert {"open_app", "click", "type_text", "draw_strokes", "done"} <= names
    assert {
        "schedule_task",
        "find_program",
        "web_search",
        "execute_subgoal",
        "run_action_batch",
        "capture_template",
        "clipboard_read",
        "mouse_down",
        "screenshot",
        "list_installed_apps",
    }.isdisjoint(names)


def test_draw_strokes_schema_hard_bounds_one_turn_geometry_budget():
    from winpilot.agent.tool_schemas import tool_schemas

    native_tools = tool_schemas(profile="lean_gui", native_vision=True)
    native_draw = next(
        tool["function"] for tool in native_tools
        if tool["function"]["name"] == "draw_strokes"
    )
    frame_strokes = native_draw["parameters"]["properties"]["frame_strokes"]
    assert frame_strokes["maxItems"] == 16
    assert frame_strokes["items"]["maxItems"] == 16

    text_tools = tool_schemas(profile="lean_gui", native_vision=False)
    text_draw = next(
        tool["function"] for tool in text_tools
        if tool["function"]["name"] == "draw_strokes"
    )
    properties = text_draw["parameters"]["properties"]
    assert properties["strokes"]["maxItems"] == 16
    assert properties["strokes"]["items"]["maxItems"] == 16
    assert properties["normalized_strokes"]["maxItems"] == 16
    assert properties["normalized_strokes"]["items"]["maxItems"] == 16


def test_legacy_drawing_profile_is_generic_lean_gui_alias():
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

    assert {"revise_plan", "complete_subgoal"} <= names
    assert {"execute_subgoal", "done", "blocked"} <= names
    assert {"subgoal", "target_window", "draw", "draws", "verify_texts", "constraints"} <= props
    assert {"input", "click", "clicks"} <= props
    assert "detailed creature" not in execute["function"]["description"]


def test_revise_plan_schema_supports_live_atomic_mutations():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(planning=True, coding_runtime=False, allow_shell=False)
    revise = next(tool for tool in tools if tool["function"]["name"] == "revise_plan")
    params = revise["function"]["parameters"]
    operation = params["properties"]["operations"]["items"]

    assert params["required"] == []
    assert set(params["properties"]) == {"steps", "operations"}
    assert params["oneOf"] == [
        {"required": ["steps"]},
        {"required": ["operations"]},
    ]
    assert "minItems" not in params["properties"]["steps"]
    assert params["properties"]["operations"]["minItems"] == 1
    assert operation["properties"]["op"]["enum"] == [
        "add", "update", "remove", "move", "reopen"
    ]
    assert operation["required"] == ["op"]
    assert operation["oneOf"] == [
        {
            "properties": {"op": {"enum": ["add"]}},
            "required": ["op", "goal"],
        },
        {
            "properties": {"op": {"enum": ["update"]}},
            "required": ["op", "index", "goal"],
        },
        {
            "properties": {"op": {"enum": ["remove"]}},
            "required": ["op", "index"],
        },
        {
            "properties": {"op": {"enum": ["move"]}},
            "required": ["op", "index", "to_index"],
        },
        {
            "properties": {"op": {"enum": ["reopen"]}},
            "required": ["op", "index"],
        },
    ]
    assert "at any time" in revise["function"]["description"]


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
        if profile == "lean_gui":
            assert {
                "execute_subgoal",
                "run_action_batch",
                "read_text",
                "screen_watch",
                "screenshot",
                "zoom",
            }.isdisjoint(names)

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

        draw_tool = by_name(native, "draw_strokes")["function"]
        draw = draw_tool["parameters"]
        assert "frame_strokes" in draw["required"]
        assert "strokes" not in draw["properties"]
        assert "normalized_strokes" not in draw["properties"]
        assert "oneOf" not in draw
        assert "work_surface_frame_bounds" in draw_tool["description"]
        assert "whole-frame bounds" in draw_tool["description"]
        assert "every authored point" in draw_tool["description"]
        compact_path_contract = (
            draw_tool["description"]
            + " "
            + draw["properties"]["frame_strokes"]["description"]
        )
        assert "4-16 points per path" in compact_path_contract
        assert "connects" in compact_path_contract
        assert "equal-step" in compact_path_contract

        if profile == "standard":
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
    draw = by_name(text_only, "draw_strokes")["function"]["parameters"]
    assert draw["required"] == []
    assert {"strokes", "normalized_strokes"} <= set(draw["properties"])
    assert draw["oneOf"] == [
        {"required": ["strokes"]},
        {"required": ["normalized_strokes"]},
    ]
    normalized_point = draw["properties"]["normalized_strokes"]["items"]["items"]
    assert normalized_point["minItems"] == 2
    assert normalized_point["maxItems"] == 2
    assert normalized_point["items"]["minimum"] == 0
    assert normalized_point["items"]["maximum"] == 1000


def test_action_batch_projects_every_controller_action_as_typed_variant():
    from winpilot.agent.tool_schemas import tool_schemas
    from winpilot.fast.action_contract import supported_actions

    batch = next(
        tool["function"] for tool in tool_schemas(native_vision=False)
        if tool["function"]["name"] == "run_action_batch"
    )
    item = batch["parameters"]["properties"]["actions"]["items"]
    variants = {
        variant["properties"]["action"]["const"]: variant
        for variant in item["oneOf"]
    }

    assert set(variants) == set(supported_actions())
    assert variants["select_color"]["properties"]["color"]["type"] == "string"
    assert "color" in variants["select_color"]["required"]
    visual_wait = variants["wait_for_visual_change"]["properties"]
    assert visual_wait["region"]["required"] == ["x", "y", "width", "height"]
    assert {"timeout_s", "min_ratio", "required"} <= set(visual_wait)


def test_native_action_batch_typed_variants_use_frame_coordinates():
    from winpilot.agent.tool_schemas import tool_schemas

    batch = next(
        tool["function"] for tool in tool_schemas(native_vision=True)
        if tool["function"]["name"] == "run_action_batch"
    )
    variants = {
        variant["properties"]["action"]["const"]: variant
        for variant in batch["parameters"]["properties"]["actions"]["items"]["oneOf"]
    }
    drag = variants["drag"]["properties"]
    assert {"frame_x1", "frame_y1", "frame_x2", "frame_y2", "frame_points"} <= set(drag)
    assert {"x1", "y1", "x2", "y2", "points"}.isdisjoint(drag)
    assert "frame_strokes" in variants["draw_strokes"]["properties"]
    visual_wait = variants["wait_for_visual_change"]["properties"]
    assert "frame_region" in visual_wait
    assert "region" not in visual_wait
    assert "effect_frame_region" in variants["drag"]["properties"]
    assert "effect_region" not in variants["drag"]["properties"]


def test_native_vision_lean_profile_has_small_single_action_operator():
    import json

    from winpilot.agent import tool_schemas as schemas

    native = schemas.tool_schemas(
        planning=True,
        native_vision=True,
        allow_shell=False,
        allow_files=False,
        profile="lean_gui",
    )
    text_only = schemas.tool_schemas(
        planning=True,
        native_vision=False,
        allow_shell=False,
        allow_files=False,
        profile="lean_gui",
    )
    names = set(_names(native))

    assert {
        "open_app",
        "focus_window",
        "observe",
        "click",
        "type_text",
        "hotkey",
        "scroll",
        "drag",
        "draw_strokes",
        "revise_plan",
        "complete_subgoal",
        "done",
        "blocked",
    } <= names
    assert len(native) < len(text_only)
    assert len(json.dumps(native, ensure_ascii=False)) < 10_000
