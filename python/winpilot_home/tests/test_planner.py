from winpilot.agent import planner


def test_plan_drops_button_label_echo_text_check():
    plan = planner.Plan(
        [
            {
                "goal": "Click the Apply/Input button",
                "check": {"text_appears": "Apply/Input"},
            }
        ]
    )

    assert plan.steps[0]["check"] is None


def test_plan_keeps_real_state_text_after_click():
    plan = planner.Plan(
        [
            {
                "goal": "Click the local counter button until LOCAL_COUNTER appears",
                "check": {"text_appears": "LOCAL_COUNTER"},
            }
        ]
    )

    assert plan.steps[0]["check"] == {"text_appears": "LOCAL_COUNTER"}


def test_parse_steps_sanitizes_chinese_button_label_echo():
    steps = planner._parse_steps(
        '[{"goal":"点击应用输入按钮使文字生效","check":{"text_appears":"应用输入"}}]'
    )

    assert steps == [{"goal": "点击应用输入按钮使文字生效", "check": None}]


def test_parse_steps_recovers_badly_quoted_string_list():
    steps = planner._parse_steps(
        '["打开浏览器并访问目标页面", "点击页面上的"校准点"按钮", "确认页面出现 CALIBRATION_OK"]'
    )

    assert [step["goal"] for step in steps] == [
        "打开浏览器并访问目标页面",
        '点击页面上的"校准点"按钮 and verify CALIBRATION_OK',
    ]
    assert steps[1]["check"] == {"text_appears": "CALIBRATION_OK"}


def test_plan_merges_click_then_visible_state_verify():
    plan = planner.Plan(
        [
            {"goal": "点击左侧控制区的 Local Counter 按钮"},
            {"goal": "确认页面出现 LOCAL_COUNTER 文字"},
        ]
    )

    assert len(plan.steps) == 1
    assert plan.steps[0]["check"] == {"text_appears": "LOCAL_COUNTER"}
    assert "add/update/remove/move" in plan.render()
    assert "guidance, not completion evidence" in plan.render()


def test_plan_keeps_model_authored_visual_check_as_guidance():
    plan = planner.Plan(
        [
            {
                "goal": "在画布上绘制一个蓝色三角形",
                "check": {"text_appears": "蓝色"},
            }
        ]
    )

    assert plan.steps[0]["check"] == {"text_appears": "蓝色"}


def test_plan_does_not_apply_drawing_specific_check_sanitization():
    plan = planner.Plan(
        [
            {
                "goal": "Confirm the canvas visibly contains a blue triangle and a red rectangle",
                "check": {"text_appears": "Paint"},
            }
        ]
    )

    assert plan.steps[0]["check"] == {"text_appears": "Paint"}


def test_plan_keeps_state_token_check_for_visual_confirmation():
    plan = planner.Plan(
        [
            {
                "goal": "Confirm the drawing screen shows DRAW_DONE_42",
                "check": {"text_appears": "DRAW_DONE_42"},
            }
        ]
    )

    assert plan.steps[0]["check"] == {"text_appears": "DRAW_DONE_42"}


def test_verbose_two_action_gui_task_skips_upfront_plan(monkeypatch):
    monkeypatch.setattr(planner, "planning_enabled", lambda: True)
    task = (
        "Open http://127.0.0.1/gui_agent_target.html, click the Calibration "
        "button, and confirm CALIBRATION_OK. Use real GUI input and do not use "
        "JavaScript, DOM, DevTools, shell, or script injection."
    )

    assert planner.should_plan_upfront(task) is False


def test_repeated_click_policy_word_does_not_create_extra_plan_step(monkeypatch):
    monkeypatch.setattr(planner, "planning_enabled", lambda: True)
    task = (
        "打开 http://127.0.0.1/gui_agent_target.html，点击页面上的校准点按钮，"
        "最后确认页面出现 CALIBRATION_OK。必须通过GUI真实鼠标点击，不要用脚本注入点击。"
    )

    assert planner.should_plan_upfront(task) is False


def test_four_explicit_gui_actions_still_use_upfront_plan(monkeypatch):
    monkeypatch.setattr(planner, "planning_enabled", lambda: True)

    assert planner.should_plan_upfront(
        "Open Notepad type the note save it and confirm the saved file"
    ) is True


def test_plan_operations_can_add_update_remove_and_reorder_without_losing_progress():
    plan = planner.Plan(["open source", "collect facts", "write result"])
    plan.complete(1)

    changes = plan.apply_operations([
        {"op": "add", "index": 3, "goal": "cross-check facts"},
        {"op": "update", "index": 2, "goal": "collect grounded facts"},
        {"op": "move", "index": 4, "to_index": 3},
        {"op": "remove", "index": 4},
    ])

    assert [step["goal"] for step in plan.steps] == [
        "open source", "collect grounded facts", "write result"
    ]
    assert [step["done"] for step in plan.steps] == [True, False, False]
    assert len(changes) == 4


def test_plan_operations_are_atomic_when_one_index_is_invalid():
    plan = planner.Plan(["first", "second"])

    try:
        plan.apply_operations([
            {"op": "add", "goal": "temporary"},
            {"op": "remove", "index": 99},
        ])
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("invalid mutation should fail")

    assert [step["goal"] for step in plan.steps] == ["first", "second"]
