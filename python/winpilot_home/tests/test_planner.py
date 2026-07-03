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


def test_plan_merges_click_then_visible_state_verify():
    plan = planner.Plan(
        [
            {"goal": "点击左侧控制区的 Local Counter 按钮"},
            {"goal": "确认页面出现 LOCAL_COUNTER 文字"},
        ]
    )

    assert len(plan.steps) == 1
    assert plan.steps[0]["check"] == {"text_appears": "LOCAL_COUNTER"}
    assert "GUI routine hint" in plan.render()


def test_plan_drops_drawing_color_as_text_check():
    plan = planner.Plan(
        [
            {
                "goal": "在画布上绘制一个蓝色三角形",
                "check": {"text_appears": "蓝色"},
            }
        ]
    )

    assert plan.steps[0]["check"] is None
