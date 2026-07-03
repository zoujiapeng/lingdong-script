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

