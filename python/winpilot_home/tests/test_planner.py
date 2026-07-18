from winpilot.agent import planner
from winpilot.agent import context_guidance
from winpilot.tasking.contracts import TaskIntent
from types import SimpleNamespace


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


def test_typed_intent_drives_upfront_plan_without_reparsing_task(monkeypatch):
    monkeypatch.setattr(planner, "planning_enabled", lambda: True)

    def unexpected_parse(*_args, **_kwargs):
        raise AssertionError("typed planner path must not reparse raw task text")

    from winpilot.tasking.intent import TaskIntentParser

    monkeypatch.setattr(TaskIntentParser, "parse", unexpected_parse)
    intent = TaskIntent(
        original_text="send the current image through the selected channel",
        operation=["capture", "send"],
        media_type="image",
        source="active_window",
        channel="chat",
    )

    assert planner.should_plan_upfront(
        "short wording with no sequence markers",
        task_intent=intent,
    ) is True
    assert planner.should_plan_upfront(
        "another wording",
        task_intent=intent.model_dump(mode="json"),
    ) is True


def test_context_guidance_passes_runner_typed_intent_to_planner(monkeypatch):
    typed_intent = {
        "original_text": "opaque task",
        "operation": ["capture", "send"],
        "media_type": "image",
        "source": "active_window",
        "channel": "chat",
    }
    observed: dict = {}

    def inspect_intent(task, *, task_intent=None):
        observed["task"] = task
        observed["task_intent"] = task_intent
        return False

    monkeypatch.setattr(context_guidance, "should_plan_upfront", inspect_intent)

    class Runner(context_guidance.ContextGuidanceMixin):
        task = "opaque task"
        task_intent = typed_intent

    Runner()._maybe_plan_upfront(client=None)

    assert observed == {
        "task": "opaque task",
        "task_intent": typed_intent,
    }


def test_full_desktop_stage_forces_a_provisional_plan_with_live_context(monkeypatch):
    observed: dict = {}

    def unexpected_heuristic(*_args, **_kwargs):
        raise AssertionError("a typed full desktop stage must not depend on text heuristics")

    def fake_make_plan(_client, model, task, **kwargs):
        observed.update({
            "model": model,
            "task": task,
            "runtime_context": kwargs.get("runtime_context"),
        })
        return [{"goal": "inspect the live environment before choosing a route"}]

    monkeypatch.setattr(context_guidance, "should_plan_upfront", unexpected_heuristic)
    monkeypatch.setattr(context_guidance, "make_plan", fake_make_plan)
    monkeypatch.setattr(
        context_guidance.CONFIG,
        "get",
        lambda *path, default=None: {"model": "test-model"}
        if path == ("api",) else default,
    )

    class Runner(context_guidance.ContextGuidanceMixin):
        task = "opaque adaptive desktop goal"
        task_intent = {
            "operation": ["operate"],
            "ambiguous_fields": ["channel"],
        }
        runtime_profile = SimpleNamespace(plan_upfront=True)
        session = SimpleNamespace(
            plan=None,
            snapshot=SimpleNamespace(
                title="Current App",
                elements=[SimpleNamespace(text="Visible control")],
            ),
        )
        messages = []
        run_id = "run-plan"

        @staticmethod
        def _track_aux_model_response(*_args, **_kwargs):
            return None

    runner = Runner()
    runner._maybe_plan_upfront(client=object())

    assert observed == {
        "model": "test-model",
        "task": "opaque adaptive desktop goal",
        "runtime_context": (
            "Unresolved facts to discover from the live environment: channel\n"
            "Current window title: Current App\n"
            "Currently visible labels: Visible control"
        ),
    }
    assert runner.session.plan.steps[0]["goal"] == (
        "inspect the live environment before choosing a route"
    )


def test_prebound_target_is_planner_context_and_satisfies_open_step(monkeypatch):
    observed: dict = {}

    def fake_make_plan(_client, _model, _task, **kwargs):
        observed["runtime_context"] = kwargs.get("runtime_context")
        return ["打开画图", "截取当前作品"]

    monkeypatch.setattr(context_guidance, "make_plan", fake_make_plan)
    monkeypatch.setattr(
        context_guidance.CONFIG,
        "get",
        lambda *path, default=None: {"model": "test-model"}
        if path == ("api",) else default,
    )

    class Runner(context_guidance.ContextGuidanceMixin):
        task = "capture the current work"
        task_intent = {"operation": ["capture"]}
        runtime_profile = SimpleNamespace(plan_upfront=True)
        session = SimpleNamespace(
            plan=None,
            snapshot=None,
            hwnd=321,
            target_hint="Paint",
        )
        messages = []
        run_id = "run-prebound-plan"

        @staticmethod
        def _track_aux_model_response(*_args, **_kwargs):
            return None

    runner = Runner()
    runner._maybe_plan_upfront(client=object())

    assert observed["runtime_context"] == (
        "Current target is already bound and open: Paint (hwnd=321)"
    )
    assert [step["done"] for step in runner.session.plan.steps] == [True, False]


def test_prebound_target_does_not_satisfy_opening_a_different_app():
    from winpilot.agent.gui_plan_helpers import _pure_open_goal_matches_bound_target

    assert _pure_open_goal_matches_bound_target("打开画图", "Paint") is True
    assert _pure_open_goal_matches_bound_target("打开微信", "Paint") is False
    assert _pure_open_goal_matches_bound_target("打开当前窗口", "Paint") is True


def test_should_plan_upfront_keeps_raw_parse_as_legacy_fallback(monkeypatch):
    monkeypatch.setattr(planner, "planning_enabled", lambda: True)
    calls: list[str] = []

    class Parser:
        def parse(self, task):
            calls.append(task)
            return TaskIntent(
                original_text=task,
                operation=["capture", "send"],
                media_type="image",
                source="active_window",
                channel="chat",
            )

    from winpilot.tasking import intent as intent_module

    monkeypatch.setattr(intent_module, "TaskIntentParser", Parser)

    assert planner.should_plan_upfront("legacy direct call") is True
    assert calls == ["legacy direct call"]


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
