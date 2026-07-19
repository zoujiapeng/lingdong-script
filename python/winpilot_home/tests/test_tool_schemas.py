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


def test_open_conversation_can_discover_an_unresolved_channel():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(planning=True, coding_runtime=False, allow_shell=False)
    open_conversation = next(
        tool for tool in tools
        if tool["function"]["name"] == "open_conversation"
    )["function"]

    assert open_conversation["parameters"]["required"] == ["recipient"]
    assert "app" in open_conversation["parameters"]["properties"]
    assert "omit app" in open_conversation["description"]


def test_execute_subgoal_schema_states_only_structural_preconditions():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(planning=True, coding_runtime=False, allow_shell=False)
    execute = next(tool for tool in tools if tool["function"]["name"] == "execute_subgoal")

    assert "verification-only routine requires a bound target window" in execute["function"]["description"]
    assert "subgoal is only a label" in execute["function"]["description"]
    assert "not executable by itself" in execute["function"]["parameters"]["properties"]["subgoal"]["description"]


def test_execute_subgoal_schema_describes_fields_without_ui_tactics():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(planning=True, coding_runtime=False, allow_shell=False)
    execute = next(tool for tool in tools if tool["function"]["name"] == "execute_subgoal")
    description = execute["function"]["description"]

    assert "declared input, click, format, draw, wait, or verification fields" in description
    assert "keypad" not in description.casefold()
    assert "one hotkey per" not in description.casefold()


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


def test_dynamic_pointer_primitives_are_top_level_and_batch_validated():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(
        planning=True,
        coding_runtime=False,
        allow_shell=False,
        allow_files=False,
        profile="standard",
    )
    names = set(_names(tools))
    assert {"move_mouse", "mouse_down", "mouse_up"} <= names
    assert "hold_key" not in names

    batch = next(
        tool["function"] for tool in tools
        if tool["function"]["name"] == "run_action_batch"
    )
    variants = {
        variant["properties"]["action"]["const"]
        for variant in batch["parameters"]["properties"]["actions"]["items"]["oneOf"]
    }
    assert {"move_mouse", "mouse_down", "mouse_up", "hold_key"} <= variants
    pointer_session = batch["parameters"]["properties"]["pointer_session"]
    assert set(pointer_session["properties"]) == {"before", "after"}
    assert pointer_session["properties"]["after"]["enum"] == [
        "", "left", "right", "middle",
    ]


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
        "move_mouse",
        "mouse_down",
        "mouse_up",
    } <= names
    assert {"find_app", "find_program", "tray_click"}.isdisjoint(names)
    assert {
        "capture_template",
        "find_image",
        "color_probe",
        "clipboard_read",
        "list_installed_apps",
    }.isdisjoint(names)


def test_communication_task_uses_lean_gui_profile():
    from winpilot.agent.gui_plan_helpers import _schema_profile_for_task

    assert _schema_profile_for_task(
        'Open Nimbus Messenger, send "HELLO" to contact Alpha, and verify it in the conversation.',
        is_coding=False,
    ) == "lean_gui"


def test_schema_profile_auto_uses_lean_for_gui_and_communication_tasks(monkeypatch):
    from winpilot.agent.loop import _schema_profile_for_task

    monkeypatch.setattr(
        "winpilot.agent.loop.CONFIG.get",
        lambda *path, default=None: "auto" if path == ("agent", "tool_profile") else default,
    )

    assert _schema_profile_for_task(
        "Use GUI only to open Aurora Canvas and draw a blue triangle.",
        is_coding=False,
    ) == "lean_gui"
    assert _schema_profile_for_task(
        'Open Nimbus Messenger and send "HELLO" to contact Alpha.',
        is_coding=False,
    ) == "lean_gui"
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

    # Classification comes from the structured desktop intent, not an app or
    # benchmark-subject catalogue.
    dog_prompt = "Use Aurora Canvas to sketch a polished fictional creature."
    assert _schema_profile_for_task(
        dog_prompt,
        is_coding=False,
        native_vision=True,
    ) == "lean_gui"
    assert _schema_profile_for_task(
        "Open Quill Editor, type the meeting title, and click Save.",
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
        "Open Quill Editor, type hello, and click Save.",
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
        "Open Aurora Canvas and draw a landscape on its work surface.",
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

    task = "Open Quill Editor, type hello, and click Save."
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


def test_native_gui_auto_profile_keeps_one_compact_generic_operator(monkeypatch):
    from winpilot.agent.gui_plan_helpers import _schema_profile_for_task
    from winpilot.agent.tool_schemas import tool_schemas

    monkeypatch.setattr(
        "winpilot.agent.gui_plan_helpers.CONFIG.get",
        lambda *path, default=None: "auto" if path == ("agent", "tool_profile") else default,
    )
    profile = _schema_profile_for_task(
        "Open Quill Editor, type hello, and click Save.",
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

    assert len(tools) == 22
    assert {
        "open_app", "click", "double_click", "type_text", "draw_strokes", "move_mouse",
        "mouse_down", "mouse_up", "done",
    } <= names
    assert {
        "schedule_task",
        "find_program",
        "web_search",
        "execute_subgoal",
        "run_action_batch",
        "capture_template",
        "clipboard_read",
        "screenshot",
        "list_installed_apps",
    }.isdisjoint(names)


def test_native_dynamic_pointer_motion_requires_coordinates_but_release_does_not():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(profile="lean_gui", native_vision=True)
    by_name = {tool["function"]["name"]: tool["function"] for tool in tools}

    for name in ("move_mouse", "mouse_down"):
        params = by_name[name]["parameters"]
        props = params["properties"]
        assert {"x", "y"}.isdisjoint(props)
        assert {"image_x", "image_y"} <= set(props)
        assert {"frame_x", "frame_y"}.isdisjoint(props)
        assert {"image_x", "image_y"} <= set(params["required"])
        assert "oneOf" not in params

    release = by_name["mouse_up"]
    assert release["parameters"]["required"] == []
    assert "oneOf" not in release["parameters"]
    assert "release at its current position" in release["description"]


def test_native_click_primitives_have_distinct_unambiguous_pixel_schemas():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(profile="lean_gui", native_vision=True)
    by_name = {tool["function"]["name"]: tool["function"] for tool in tools}

    for name in ("click", "double_click"):
        params = by_name[name]["parameters"]
        props = params["properties"]
        assert {"image_x", "image_y"} <= set(props)
        assert {"x", "y", "frame_x", "frame_y", "double"}.isdisjoint(props)
        assert {"image_x", "image_y"} <= set(params["required"])
        assert "oneOf" not in params

    assert by_name["click"]["description"].startswith("Click one visible point")
    assert by_name["double_click"]["description"].startswith("Double-click one visible point")


def test_draw_strokes_schema_hard_bounds_one_turn_geometry_budget():
    from winpilot.agent.tool_schemas import tool_schemas

    native_tools = tool_schemas(profile="lean_gui", native_vision=True)
    native_draw = next(
        tool["function"] for tool in native_tools
        if tool["function"]["name"] == "draw_strokes"
    )
    image_strokes = native_draw["parameters"]["properties"]["image_strokes"]
    assert image_strokes["maxItems"] == 16
    assert image_strokes["items"]["maxItems"] == 16

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


def test_lean_revise_plan_keeps_lossless_full_replacement_only():
    from winpilot.agent.tool_schemas import tool_schemas

    tools = tool_schemas(
        planning=True,
        coding_runtime=False,
        allow_shell=False,
        profile="lean_gui",
    )
    revise = next(tool for tool in tools if tool["function"]["name"] == "revise_plan")
    params = revise["function"]["parameters"]

    assert set(params["properties"]) == {"steps"}
    assert params["required"] == ["steps"]
    assert "oneOf" not in params
    assert "complete ordered steps" in revise["function"]["description"]


def test_native_vision_hides_redundant_secondary_vlm_tools(monkeypatch):
    from winpilot.agent import tool_schemas as schemas

    monkeypatch.setattr(schemas.vlm, "is_configured", lambda: True)
    text_only = set(_names(schemas.tool_schemas(native_vision=False, allow_shell=False)))
    native = set(_names(schemas.tool_schemas(native_vision=True, allow_shell=False)))

    assert {"vlm_describe", "vlm_verify"} <= text_only
    assert {"vlm_describe", "vlm_verify"}.isdisjoint(native)


def test_native_vision_exposes_one_exact_submitted_image_coordinate_system():
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

        observe = by_name(native, "observe")["function"]
        assert set(observe["parameters"]["properties"]) <= {"hwnd"}
        assert "query" not in observe["description"].casefold()
        assert "structured" not in observe["description"].casefold()

        click = by_name(native, "click")["function"]["parameters"]
        assert {"image_x", "image_y"} <= set(click["properties"])
        assert {"frame_x", "frame_y"}.isdisjoint(click["properties"])
        assert {"x", "y"}.isdisjoint(click["properties"])
        assert {
            "element_id", "label", "text_query", "visual_anchor", "fallback_text",
        }.isdisjoint(click["properties"])
        assert {"image_x", "image_y"} <= set(click["required"])
        assert "oneOf" not in click
        assert "maximum" not in click["properties"]["image_x"]
        assert "maximum" not in click["properties"]["image_y"]

        type_text = by_name(native, "type_text")["function"]
        assert set(type_text["parameters"]["properties"]) == {"text", "expect"}

        scroll = by_name(native, "scroll")["function"]["parameters"]
        assert set(scroll["required"]) == {"amount", "image_x", "image_y"}
        assert {"image_x", "image_y", "amount"} <= set(scroll["required"])
        assert "oneOf" not in scroll
        assert {"x", "y"}.isdisjoint(scroll["properties"])

        drag = by_name(native, "drag")["function"]["parameters"]
        assert set(drag["required"]) == {
            "image_x1", "image_y1", "image_x2", "image_y2",
        }
        assert "oneOf" not in drag
        assert {"x1", "y1", "x2", "y2", "points"}.isdisjoint(drag["properties"])
        assert "image_points" in drag["properties"]
        assert drag["properties"]["image_points"]["maxItems"] == 32
        assert "frame_points" not in drag["properties"]
        assert "maximum" not in drag["properties"]["image_x2"]

        draw_tool = by_name(native, "draw_strokes")["function"]
        draw = draw_tool["parameters"]
        assert "image_strokes" in draw["required"]
        assert "strokes" not in draw["properties"]
        assert "normalized_strokes" not in draw["properties"]
        assert "oneOf" not in draw
        assert "current-full-image pixels" in draw_tool["description"]
        compact_path_contract = (
            draw_tool["description"]
            + " "
            + draw["properties"]["image_strokes"]["description"]
        )
        assert "consecutive points are joined" in compact_path_contract
        for tactic in ("handwriting", "signature", "canvas", "composition", "equal-step"):
            assert tactic not in compact_path_contract.casefold()

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
            assert "at most one spatial mutation" in batch_text
            assert "coordinate source" in batch_text
            for tactic in ("semantic target", "targeted query", "direct click", "post-action frame"):
                assert tactic not in batch_text.casefold()

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


def test_native_visual_schema_contains_no_example_specific_tactics():
    import json

    from winpilot.agent.tool_schemas import tool_schemas

    serialized = json.dumps(
        tool_schemas(
            planning=True,
            native_vision=True,
            allow_shell=False,
            allow_files=False,
            profile="lean_gui",
        ),
        ensure_ascii=False,
    ).casefold()
    for tactic in (
        "calculator", "keypad", "handwriting", "signature", "game piece",
        "slider", "shared silhouette", "scene plan", "search box",
    ):
        assert tactic not in serialized


def test_native_wait_exposes_duration_visual_change_and_compact_conditions():
    from winpilot.agent.tool_schemas import tool_schemas

    wait = next(
        tool["function"]
        for tool in tool_schemas(
            planning=True,
            native_vision=True,
            allow_shell=False,
            allow_files=False,
            profile="lean_gui",
        )
        if tool["function"]["name"] == "wait"
    )
    parameters = wait["parameters"]

    assert parameters["required"] == []
    assert parameters["anyOf"] == [
        {"required": ["duration_s"]},
        {"required": ["until_change"]},
        {"required": ["condition"]},
    ]
    assert parameters["properties"]["duration_s"]["maximum"] == 120
    assert parameters["properties"]["until_change"]["type"] == "boolean"
    assert parameters["properties"]["min_change_ratio"]["maximum"] == 1
    assert {
        "screen_stable",
        "text_appears",
        "text_gone",
        "window_appears",
        "window_gone",
        "window_title",
    } <= set(parameters["properties"]["condition"]["properties"])


def test_native_visual_prompt_ignores_compiler_tactics_and_historical_recipes():
    from winpilot.agent.prompts import system_prompt
    from winpilot.agent.runtime_profile import RuntimeProfile

    tactic = "click the search box and switch to a semantic fallback"
    native = system_prompt(
        main_model_has_vision=True,
        planning_enabled=True,
        runtime_section=RuntimeProfile(stage_guidance=tactic).prompt_section(),
        playbook_text="WINDOW PLAYBOOK TACTIC",
        experience_hints="PAST EXPERIENCE TACTIC",
    )
    text_only = system_prompt(
        main_model_has_vision=False,
        playbook_text="WINDOW PLAYBOOK TACTIC",
        experience_hints="PAST EXPERIENCE TACTIC",
    )

    assert "re-understand the latest frame" in native
    assert tactic not in native
    assert "WINDOW PLAYBOOK TACTIC" not in native
    assert "PAST EXPERIENCE TACTIC" not in native
    assert "action family" not in native
    assert "WINDOW PLAYBOOK TACTIC" in text_only
    assert "PAST EXPERIENCE TACTIC" in text_only


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
    assert "ensure_document_surface" not in variants
    assert variants["select_color"]["properties"]["color"]["type"] == "string"
    assert "color" in variants["select_color"]["required"]
    visual_wait = variants["wait_for_visual_change"]["properties"]
    assert visual_wait["region"]["required"] == ["x", "y", "width", "height"]
    assert {"timeout_s", "min_ratio", "required"} <= set(visual_wait)


def test_native_action_batch_typed_variants_use_submitted_image_coordinates():
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
    assert {"image_x1", "image_y1", "image_x2", "image_y2", "image_points"} <= set(drag)
    assert {"x1", "y1", "x2", "y2", "points"}.isdisjoint(drag)
    assert "image_strokes" in variants["draw_strokes"]["properties"]
    visual_wait = variants["wait_for_visual_change"]["properties"]
    assert "image_region" in visual_wait
    assert "region" not in visual_wait
    assert "effect_image_region" in variants["drag"]["properties"]
    assert "effect_region" not in variants["drag"]["properties"]


def test_native_done_stops_at_proven_outcome_without_cosmetic_cleanup():
    from winpilot.agent.tool_schemas import tool_schemas

    done = next(
        tool["function"] for tool in tool_schemas(native_vision=True)
        if tool["function"]["name"] == "done"
    )

    description = done["description"]
    assert "Finish immediately" in description
    assert "do not add unrequested cleanup" in description


def test_direct_spatial_tools_expose_generic_hard_region_constraints():
    from winpilot.agent.tool_schemas import tool_schemas

    native = {
        tool["function"]["name"]: tool["function"]
        for tool in tool_schemas(native_vision=True)
    }
    text_only = {
        tool["function"]["name"]: tool["function"]
        for tool in tool_schemas(native_vision=False)
    }
    for name in (
        "click", "scroll", "move_mouse", "mouse_down", "mouse_up", "drag",
        "draw_strokes",
    ):
        native_props = native[name]["parameters"]["properties"]
        text_props = text_only[name]["parameters"]["properties"]
        assert "constraint_image_region" in native_props
        assert "constraint_region" not in native_props
        assert "constraint_region" in text_props
        assert "constraint_image_region" not in text_props

    batch = native["run_action_batch"]["parameters"]["properties"]["actions"]
    variants = {
        item["properties"]["action"]["const"]: item["properties"]
        for item in batch["items"]["oneOf"]
    }
    for name in ("move_mouse", "mouse_down", "mouse_up", "drag", "draw_strokes"):
        assert "constraint_image_region" in variants[name]


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
    # Native vision receives one exact submitted-image pixel contract. Keep it
    # compact enough for one decision turn.
    # Three explicit cross-frame pointer transitions add a small fixed schema
    # cost but avoid hiding dynamic steering inside an opaque macro.
    assert len(json.dumps(native, ensure_ascii=False)) < 13_000
