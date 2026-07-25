from __future__ import annotations

from pathlib import Path
import time

import pytest


TASKS = (
    "用绘图岛画一只猫然后截图用星河信使发给我的手机",
    "把刚画的猫截图通过星河信使发给我的手机",
    "在绘图岛应用里画猫，截取绘图岛窗口，通过星河信使发给我的手机",
)


def _contract_test_provider_type():
    from winpilot.tasking.adapters import CommunicationAdapter

    class ContractTestProvider(CommunicationAdapter):
        id = "contract-test"
        app_name = "Contract Test Messenger"
        channels = ("星河信使", "云桥")

        def open_conversation(self, recipient: str, *, stage_id: str = ""):
            return {"ok": False, "error": "compile-only test provider"}

        def resolve_composer(self):
            return {"ok": False, "error": "compile-only test provider"}

        def paste_or_attach_artifact(self, artifact_id: str, *, stage_id: str = ""):
            return {"ok": False, "error": "compile-only test provider"}

        def send_current_composer(self, artifact_id: str = "", *, stage_id: str = ""):
            return {"ok": False, "error": "compile-only test provider"}

        def send_text(self, text: str, *, stage_id: str = ""):
            return {"ok": False, "error": "compile-only test provider"}

        def verify_latest_outbound(
            self,
            *,
            artifact_id: str = "",
            text: str = "",
            stage_id: str = "",
        ):
            return {"ok": False, "error": "compile-only test provider"}

    return ContractTestProvider


def _compile(task: str, *, cold_start: bool = False):
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.adapters import DomainAdapterRegistry

    previous_types = dict(DomainAdapterRegistry._types)
    previous_names = dict(DomainAdapterRegistry._channel_names)
    try:
        DomainAdapterRegistry.register(
            _contract_test_provider_type(),
            replace=True,
        )
        return TaskContractCompiler().compile_graph(task, cold_start=cold_start)
    finally:
        DomainAdapterRegistry._types.clear()
        DomainAdapterRegistry._types.update(previous_types)
        DomainAdapterRegistry._channel_names.clear()
        DomainAdapterRegistry._channel_names.update(previous_names)


def test_structured_intent_treats_send_synonyms_equally():
    from winpilot.tasking.intent import TaskIntentParser

    parser = TaskIntentParser()
    variants = (
        "把截图用星河信使发给我的手机",
        "把截图通过星河信使发送给我的手机",
        "通过星河信使给我的手机发截图",
        "把截图传给星河信使里的我的手机",
    )
    intents = [parser.parse(item) for item in variants]
    assert all("send" in item.operation for item in intents)
    assert all(item.media_type == "image" for item in intents)
    assert all(item.channel == "星河信使" for item in intents)
    assert all(item.recipient == "我的手机" for item in intents)


def test_structured_intent_extracts_open_ended_destination_relation():
    from winpilot.tasking.intent import TaskIntentParser

    intent = TaskIntentParser().parse("把图片发到云桥里的文件中转站")
    assert intent.operation == ["send"]
    assert intent.media_type == "image"
    assert intent.channel == "云桥"
    assert intent.recipient == "文件中转站"


def test_structured_intent_owns_chat_execution_mode():
    from winpilot.tasking.intent import TaskIntentParser

    parser = TaskIntentParser()

    assert parser.parse("keep chatting with Alice continuously").send_mode == "continuous_chat"
    assert parser.parse("chat with Alice").send_mode == "bounded_chat"
    assert parser.parse("send a message to Alice").send_mode == "send"


def test_text_send_compiles_payload_and_typed_stages():
    contract, _graph = _compile("用星河信使给测试联系人发消息“你好”")
    assert [stage.type.value for stage in contract.stages] == [
        "ReviewOrAuthorize", "OpenConversation", "SendText", "VerifyDelivery",
    ]
    send = contract.stages[2]
    assert send.parameters["text"] == "你好"
    assert send.parameters["message_ref"] == "authorized_outbound_message"


@pytest.mark.parametrize(
    ("task", "expected_source", "expected_text"),
    [
        ("帮我用星河信使给他发一句问候", "generate", ""),
        ("用星河信使回复他说我晚点到", "explicit", "我晚点到"),
        ("用星河信使给我的手机解释一下今天的安排", "generate", ""),
    ],
)
def test_text_send_never_compiles_an_empty_unowned_payload(
    task: str,
    expected_source: str,
    expected_text: str,
):
    contract, _graph = _compile(task)
    types = [stage.type.value for stage in contract.stages]
    if expected_source == "generate":
        assert types[:2] == ["GenerateMessageContent", "ReviewOrAuthorize"]
    else:
        assert types[0] == "ReviewOrAuthorize"
        assert "GenerateMessageContent" not in types
    assert types[-3:] == ["OpenConversation", "SendText", "VerifyDelivery"]
    review = next(stage for stage in contract.stages if stage.type.value == "ReviewOrAuthorize")
    send = next(stage for stage in contract.stages if stage.type.value == "SendText")
    assert review.parameters["content_source"] == expected_source
    assert review.parameters["text"] == expected_text
    assert send.parameters["message_ref"] == "authorized_outbound_message"
    assert send.state_inputs[0].state_type == "authorized_message_content"


@pytest.mark.parametrize(
    ("task", "stage_type", "required_tool", "coding"),
    [
        ("分析这个GitHub项目并修复代码", "ExecuteWorkspaceTask", "apply_patch", True),
        ("搜索最新资料", "ResearchInformation", "web_search", False),
        ("处理这个CSV", "TransformData", "read_file", True),
        ("运行测试", "ExecuteWorkspaceTask", "run_shell", True),
        ("创建一个计划任务", "ManageAutomation", "schedule_task", False),
    ],
)
def test_non_gui_tasks_compile_to_capability_typed_runtimes(
    task: str,
    stage_type: str,
    required_tool: str,
    coding: bool,
):
    from winpilot.agent.runtime_profile import build_runtime_profile

    contract, graph = _compile(task)
    assert [stage.type.value for stage in contract.stages] == [stage_type]
    assert "CreateOrEditArtifact" not in [stage.type.value for stage in contract.stages]
    stage = contract.stages[0]
    assert stage.execution is not None
    assert required_tool in stage.execution.tools
    assert stage.execution.missing_capabilities == []
    profile = build_runtime_profile(task, task_contract=contract, stage=stage)
    assert profile.coding is coding
    assert profile.desktop is False
    assert graph.metadata["routing"] == "typed-stage-runtime"


def test_message_authorization_fails_closed_for_empty_or_missing_context():
    from winpilot.tasking.message_content import (
        MessageDraft,
        authorize_message_content,
    )

    assert authorize_message_content(
        text="", content_source="explicit",
    )["failure_type"] == "empty_message_content"
    missing = MessageDraft(
        text="",
        confidence=0.95,
        grounded=False,
        requires_user_input=True,
        missing_information=["today's schedule"],
    )
    verdict = authorize_message_content(
        text="", content_source="generate", generated_draft=missing,
    )
    assert verdict["ok"] is False
    assert verdict["failure_type"] == "message_context_missing"
    greeting = MessageDraft(
        text="你好，祝你今天愉快！",
        confidence=0.9,
        grounded=True,
    )
    verdict = authorize_message_content(
        text="", content_source="generate", generated_draft=greeting,
    )
    assert verdict == {
        "ok": True,
        "text": "你好，祝你今天愉快！",
        "content_source": "generate",
        "confidence": 0.9,
    }


def test_message_stage_runtime_passes_one_current_run_body_to_adapter(monkeypatch):
    import threading

    from winpilot.tasking.message_content import MessageDraft
    from winpilot.tasking.stage_runtime import StageRuntime

    contract, _graph = _compile("帮我用星河信使给他发一句问候", cold_start=True)
    generate, review, _open, send, _verify = contract.stages
    runtime = StageRuntime()
    records = []

    class Registry:
        def record(self, predicate, **values):
            record = {"id": f"evidence-{len(records)}", "predicate": predicate, **values}
            records.append(record)
            return record

    sent = []

    class Adapter:
        def send_text(self, text, *, stage_id):
            sent.append((text, stage_id))
            return {"ok": True, "send_action": True}

    registry = Registry()
    monkeypatch.setattr(runtime, "_evidence", lambda _context: registry)
    monkeypatch.setattr(runtime, "_store", lambda _context: object())
    monkeypatch.setattr(runtime, "_adapter", lambda *_args, **_kwargs: Adapter())
    monkeypatch.setattr(
        "winpilot.tasking.stage_runtime.generate_message_content",
        lambda **_kwargs: MessageDraft(
            text="你好，祝你今天愉快！",
            confidence=0.92,
            grounded=True,
            model_elapsed_ms=8,
        ),
    )
    context = {"run_id": "run-message-stages", "runtime_state": {}}
    stop = threading.Event()

    for stage in (generate, review, send):
        result = runtime._execute(stage, contract, context, stop, time.monotonic())
        assert result.ok is True

    assert sent == [("你好，祝你今天愉快！", send.id)]
    assert context["runtime_state"]["sent_text"] == "你好，祝你今天愉快！"
    assert [item["predicate"] for item in records] == [
        "message.content_generated",
        "message.content_authorized",
        "delivery.real_send_action",
    ]


def test_send_text_never_calls_adapter_without_current_task_authorization(monkeypatch):
    import threading

    from winpilot.tasking.stage_runtime import StageRuntime

    contract, _graph = _compile("用星河信使给测试联系人发消息“你好”", cold_start=True)
    send = next(stage for stage in contract.stages if stage.type.value == "SendText")
    runtime = StageRuntime()
    monkeypatch.setattr(runtime, "_evidence", lambda _context: object())
    monkeypatch.setattr(runtime, "_store", lambda _context: object())
    monkeypatch.setattr(
        runtime,
        "_adapter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("adapter must not be reached")
        ),
    )
    context = {"run_id": "run-no-auth", "runtime_state": {}}

    with pytest.raises(RuntimeError) as caught:
        runtime._execute(
            send, contract, context, threading.Event(), time.monotonic(),
        )
    assert caught.value.failure_type == "message_authorization_missing"


def test_cross_application_artifact_tasks_keep_one_lossless_desktop_goal():
    contracts = [_compile(task)[0] for task in TASKS]
    for index in (0, 2):
        contract = contracts[index]
        assert [stage.type.value for stage in contract.stages] == [
            "ExecuteDesktopTask",
        ]
        assert contract.intent.channel == "星河信使"
        assert contract.intent.recipient == "我的手机"
        assert contract.intent.media_type == "image"
        assert contract.satisfiable is True
        stage = contract.stages[0]
        assert stage.parameters["goal"] == contract.task
        assert stage.parameters["original_task"] == contract.task
        assert stage.parameters["preserve_original_intent"] is True
        assert {
            "surface_output_mode", "surface_output_subject",
            "surface_output_quality", "subject", "quality",
        }.isdisjoint(stage.parameters)
        assert contract.final_conditions == ["desktop.full_goal_verified"]

    # A capture-and-send request with no requested desktop mutation remains a
    # deterministic artifact workflow; it is not a parser-created drawing
    # mode and does not weaken the full-goal desktop path above.
    assert [stage.type.value for stage in contracts[1].stages] == [
        "CaptureArtifact", "OpenConversation", "SendArtifact", "VerifyDelivery",
    ]
    assert "DrawArtifact" not in [stage.type.value for stage in contracts[1].stages]


def test_compiled_frontend_graph_has_no_whole_task_agent_goal():
    contract, graph = _compile(TASKS[0])
    kinds = [node.kind.value for node in graph.nodes]
    assert "task_stage" in kinds
    assert "agent_goal" not in kinds
    assert graph.metadata["routing"] == "typed-stage-runtime"
    assert graph.metadata["task_contract"]["task_id"] == contract.task_id


def test_runtime_snapshot_keeps_every_evidence_and_artifact_item_typed():
    from types import SimpleNamespace

    from winpilot.orchestrator.runtime import ArpExecution

    _contract, graph = _compile(TASKS[0], cold_start=True)
    execution = ArpExecution(graph, run_id="arp-typed-snapshot")
    records = [
        {
            "id": f"evidence-{index}",
            "predicate": f"predicate.{index}",
            "verified": True,
            "provider": "test",
            "metadata": {"large": "x" * 4000, "nested": list(range(300))},
        }
        for index in range(9)
    ]
    artifacts = [
        {
            "artifact_id": f"artifact-{index}",
            "run_id": execution.run_id,
            "managed_path": f"C:/managed/{index}.png",
            "sha256": str(index) * 64,
            "metadata": {"large": "y" * 4000, "nested": list(range(300))},
        }
        for index in range(6)
    ]
    execution.runtime_state["evidence_registry"] = SimpleNamespace(
        snapshot=lambda: {"run_id": execution.run_id, "count": len(records), "records": records},
    )
    execution.runtime_state["artifact_store"] = SimpleNamespace(
        snapshot=lambda: {"run_id": execution.run_id, "count": len(artifacts), "artifacts": artifacts},
    )

    snapshot = execution.to_dict()
    assert snapshot["contract_evidence"]["count"] == len(records)
    assert len(snapshot["contract_evidence"]["records"]) == len(records)
    assert all(isinstance(item, dict) for item in snapshot["contract_evidence"]["records"])
    assert snapshot["contract_evidence"]["records"][-1]["predicate"] == "predicate.8"
    assert snapshot["artifacts"]["count"] == len(artifacts)
    assert len(snapshot["artifacts"]["artifacts"]) == len(artifacts)
    assert all(isinstance(item, dict) for item in snapshot["artifacts"]["artifacts"])
    assert snapshot["artifacts"]["artifacts"][-1]["artifact_id"] == "artifact-5"


def test_cold_start_contract_disables_stage_skills_and_experience():
    from winpilot.agent.runtime_profile import build_runtime_profile

    contract, graph = _compile(TASKS[0], cold_start=True)
    assert contract.experience_policy == "disabled"
    assert all(stage.allow_skill_replay is False for stage in contract.stages)
    assert all(node.kind.value != "skill_replay" for node in graph.nodes)
    assert graph.metadata["experience_policy"] == "disabled"
    surface = next(
        stage for stage in contract.stages
        if stage.type.value == "ExecuteDesktopTask"
    )
    profile = build_runtime_profile(
        "Draw the requested subject", task_contract=contract, stage=surface,
    )
    assert profile.experience_enabled is False
    assert profile.replay is False
    assert profile.adaptive_visual_interaction is True
    assert profile.surface_output_mode is False
    assert profile.inline_semantic_verification is False
    assert profile.surface_output_criteria == ""


def test_every_verifier_requirement_is_exposed_before_execution():
    from winpilot.tasking.capability import CapabilityPlanner

    for task in TASKS:
        contract, _graph = _compile(task)
        CapabilityPlanner.assert_satisfiable(contract)
        for stage in contract.stages:
            selection = stage.execution
            assert selection is not None
            required = set(selection.required_capabilities) | set(selection.verifier_requirements)
            assert required <= set(selection.exposed_capabilities), stage.id
            assert selection.missing_capabilities == []


def test_consolidated_desktop_goal_selects_one_full_goal_verifier():
    contract, _graph = _compile(TASKS[0])
    stage = contract.stages[0]
    selection = stage.execution
    assert selection is not None
    assert selection.selected_evidence_providers == {
        "desktop.full_goal_verified": "desktop_closed_loop_verifier",
    }
    assert "visual.semantic_verify" in selection.verifier_requirements
    assert selection.model_role == "semantic_stage"


def test_surface_stage_exposes_generic_spatial_tools_and_auditable_budget_timeout():
    contract, graph = _compile(TASKS[0])
    stage = next(item for item in contract.stages if item.type.value == "ExecuteDesktopTask")
    assert stage.execution is not None
    assert {
        "click", "drag", "execute_subgoal", "run_action_batch", "draw_strokes",
    } <= set(stage.execution.tools)
    assert "draw_strokes" in stage.execution.controller_tools
    assert {
        "click", "drag", "draw_strokes", "mouse_down", "move_mouse", "mouse_up",
    } <= set(stage.execution.model_tools)
    assert {
        "open_conversation", "send_text", "send_artifact", "verify_delivery",
    } <= set(stage.execution.model_tools)
    assert {
        "execute_subgoal", "run_action_batch", "read_text", "screen_watch",
        "vlm_verify", "record_facts", "revise_plan",
    }.isdisjoint(stage.execution.model_tools)
    assert "visual.adaptive_interaction" in stage.execution.required_capabilities
    node = next(item for item in graph.nodes if item.id == stage.id)
    assert node.timeout_s == min(1800, stage.parameters["max_steps"] * 30)


def test_desktop_child_intent_leaves_semantics_to_original_goal_and_live_frame():
    from winpilot.tasking.stage_runtime import StageRuntime

    contract, _graph = _compile(TASKS[0], cold_start=True)
    stage = next(item for item in contract.stages if item.type.value == "ExecuteDesktopTask")
    goal = "In the already opened Drawing Harbor, draw the compiler-resolved subject."

    intent = StageRuntime._stage_scoped_intent(contract, stage, goal)
    communication = StageRuntime._stage_communication_context(stage, intent)

    assert intent.original_text == goal
    assert intent.operation == ["operate"]
    assert intent.domain == "desktop"
    assert intent.subject == ""
    assert intent.source == ""
    assert intent.channel == ""
    assert intent.recipient == ""
    assert intent.destination == ""
    assert intent.interaction.active is False
    assert intent.provenance["stage_scope"] == f"{contract.task_id}:{stage.id}"
    assert intent.provenance["semantic_owner"] == "visual_model"
    assert intent.provenance["intent_policy"] == "opaque_original_goal"
    assert communication == {
        "active": False,
        "recipient": "",
        "channel": "",
        "send_mode": "send",
    }


def test_named_app_drag_hold_desktop_stage_does_not_precompile_a_tactic():
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.stage_runtime import StageRuntime

    task = (
        "Open LumaForge; drag the blue token to the target while holding "
        "the left mouse button."
    )
    contract, _graph = TaskContractCompiler().compile_graph(
        task,
        cold_start=True,
        require_gui=True,
    )
    stage = next(
        item for item in contract.stages
        if item.type.value == "ExecuteDesktopTask"
    )

    intent = StageRuntime._stage_scoped_intent(contract, stage, task)

    assert stage.parameters["app"] == "LumaForge"
    assert intent.original_text == task
    assert intent.operation == ["operate"]
    assert intent.source == ""
    assert intent.interaction.active is False
    assert intent.interaction.primary_action_modality == "infer_from_scene"


@pytest.mark.parametrize(
    "task",
    [
        "Open the chat settings page and explain it.",
        "Demonstrate how this chat window works without sending anything.",
        "Inspect the message history and tell me what it means.",
        "Open a game and read the message on screen.",
        "Reply is a button label; explain what it does.",
        "Open the drawing settings and explain them.",
    ],
)
def test_client_gui_goal_never_turns_parser_guesses_into_runtime_policy(task):
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.stage_runtime import StageRuntime

    contract = TaskContractCompiler().compile(
        task,
        cold_start=True,
        require_gui=True,
    )

    assert contract.task == task
    assert contract.clarification_questions == []
    assert [stage.type.value for stage in contract.stages] == [
        "ExecuteDesktopTask",
    ]
    stage = contract.stages[0]
    intent = StageRuntime._stage_scoped_intent(contract, stage, task)
    assert intent.original_text == task
    assert intent.operation == ["operate"]
    assert intent.domain == "desktop"
    assert intent.channel == ""
    assert intent.recipient == ""
    assert intent.send_mode == "send"
    assert intent.ambiguous_fields == []
    assert intent.interaction.active is False
    assert StageRuntime._stage_communication_context(stage, intent)["active"] is False


def test_semantic_child_runner_never_uses_raw_intent_parser_fallback(monkeypatch):
    import threading

    from winpilot.agent import loop as loop_mod
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.router import build_runner as real_build_runner
    from winpilot.tasking.intent import TaskIntentParser
    from winpilot.tasking.stage_runtime import StageRuntime

    contract, _graph = _compile(TASKS[0], cold_start=True)
    stage = next(item for item in contract.stages if item.type.value == "ExecuteDesktopTask")
    stage = stage.model_copy(deep=True)
    # This test exercises only semantic-child construction; application launch
    # is a separate deterministic stage concern.
    stage.parameters = {**stage.parameters, "app": ""}

    monkeypatch.setattr(loop_mod, "_main_model_has_vision", lambda: True)

    def parser_fallback_is_a_bug(*_args, **_kwargs):
        raise AssertionError("semantic child reparsed its generated raw goal")

    monkeypatch.setattr(TaskIntentParser, "parse", parser_fallback_is_a_bug)

    def successful_semantic_run(runner):
        runner.status = "done"
        runner.result_text = "semantic child complete"
        runner._done_verified = True
        runner._inline_stage_semantic_attempted = True
        runner._inline_stage_semantic_passed = True
        runner._surface_output_semantic_passed = True
        runner._tool_history = [
            ("draw_strokes", {"normalized_strokes": [[[10, 10], [20, 20]]]}, '{"ok": true}'),
        ]
        return runner.result_text

    monkeypatch.setattr(AgentRunner, "run", successful_semantic_run)
    captured = {}

    def capturing_build_runner(task, **kwargs):
        captured["task"] = task
        captured["kwargs"] = dict(kwargs)
        runner = real_build_runner(task, **kwargs)
        captured["runner"] = runner
        return runner

    monkeypatch.setattr(
        "winpilot.tasking.stage_runtime.build_runner",
        capturing_build_runner,
    )

    class Registry:
        def record(self, predicate, **values):
            return {"id": "evidence-stage-intent", "predicate": predicate, **values}

    runtime = StageRuntime()
    monkeypatch.setattr(runtime, "_evidence", lambda _context: Registry())
    monkeypatch.setattr(runtime, "_store", lambda _context: object())
    context = {"run_id": "run-stage-intent", "runtime_state": {}}

    try:
        result = runtime._execute(
            stage,
            contract,
            context,
            threading.Event(),
            time.monotonic(),
        )
        assert result.ok is True
        scoped = captured["kwargs"]["task_intent"]
        assert scoped.operation == ["operate"]
        assert scoped.channel == ""
        assert scoped.recipient == ""
        assert scoped.interaction.active is False
        assert captured["kwargs"]["communication_context"]["active"] is False
        assert captured["runner"].task_intent == {"original_text": contract.task}
    finally:
        runner = captured.get("runner")
        if runner is not None:
            runner.session.close()


def _legacy_draw_contract(task: str = "Use the visible canvas to draw a bird"):
    """Build a valid historical task-contract/1 DrawArtifact payload."""
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.contracts import TaskContract

    current = TaskContractCompiler().compile(task, require_gui=True)
    payload = current.model_dump(mode="json")
    stage = payload["stages"][0]
    stage["type"] = "DrawArtifact"
    stage["name"] = "legacy visual artifact stage"
    stage["parameters"] = {
        "app": "Drawing Harbor",
        "subject": "bird",
        "quality": "normal",
        "max_steps": 12,
    }
    stage["execution"]["model_role"] = "visual_artifact_planner"
    stage["execution"]["model_tools"] = [
        "execute_subgoal", "done", "blocked", "fail",
    ]
    legacy_obligation = {
        "id": "artifact.semantic_visible",
        "predicate": "artifact.semantic_visible",
        "description": "legacy visible artifact completion",
        "stage_id": stage["id"],
        "evidence_providers": [{
            "id": "semantic_visual_judge",
            "capabilities": ["visual.semantic_verify"],
            "description": "historical provider name",
        }],
        "artifact_ref": "",
        "recipient": "",
    }
    stage["obligations"] = [legacy_obligation]
    payload["obligations"] = [legacy_obligation]
    payload["final_conditions"] = ["artifact.semantic_visible"]
    for requirement in payload.get("requirements") or []:
        requirement["completion_predicates"] = ["artifact.semantic_visible"]
    return TaskContract.model_validate(payload)


def test_legacy_drawartifact_contract_remains_readable_but_capabilities_are_generic():
    from winpilot.tasking.capability import CapabilityPlanner
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.contracts import StageType

    legacy = _legacy_draw_contract()
    legacy_stage = legacy.stages[0]
    assert legacy_stage.type == StageType.DRAW_ARTIFACT

    selection = CapabilityPlanner().plan_stage(legacy_stage)
    current = TaskContractCompiler().compile(
        "Use the visible canvas to draw a bird",
        cold_start=True,
        require_gui=True,
    )
    current_stage = current.stages[0]
    assert selection.model_role == "semantic_stage"
    assert selection.model_tools == current_stage.execution.model_tools
    assert {"click", "drag", "draw_strokes", "mouse_down", "move_mouse", "mouse_up"} <= set(
        selection.model_tools
    )


def test_legacy_drawartifact_runtime_profile_normalizes_to_desktop_task():
    from winpilot.agent.runtime_profile import build_runtime_profile

    contract = _legacy_draw_contract()
    profile = build_runtime_profile(
        contract.task,
        task_contract=contract,
        stage=contract.stages[0],
        inline_semantic_verification=True,
    )
    assert profile.stage_type == "ExecuteDesktopTask"
    assert profile.adaptive_visual_interaction is True
    assert profile.plan_upfront is True
    assert profile.perception_daemon is False
    assert profile.surface_output_mode is False
    assert profile.surface_output_criteria == ""
    assert profile.inline_semantic_verification is False


def test_legacy_drawartifact_stage_executes_original_task_through_generic_runner(
    monkeypatch,
):
    import threading

    import winpilot.tasking.stage_runtime as stage_runtime
    from winpilot.tasking.completion import CompletionContractVerifier
    from winpilot.tasking.evidence import EvidenceRegistry

    contract = _legacy_draw_contract(
        "Use the visible canvas to draw a bird, then inspect the result",
    )
    stage = contract.stages[0]
    captured = {}
    pause_controller = object()
    registered_runners = []
    registry = EvidenceRegistry("legacy-draw-run")

    class FakeRunner:
        run_id = "legacy-draw-child"
        status = "done"
        result_text = "complete"
        usage_total = {}
        model_request_count = 1
        model_elapsed_ms = 7
        _done_verified = True
        _tool_history = [
            ("draw_strokes", {"image_strokes": [[[1, 1], [2, 2]]]}, '{"ok": true}'),
        ]

        def bind_target_window(self, _hwnd, _hint):
            raise AssertionError("legacy DrawArtifact must not be pre-opened by another owner")

        def run(self):
            return "complete"

    def fake_build_runner(task, **kwargs):
        captured["task"] = task
        captured["profile"] = kwargs["runtime_profile"]
        captured["intent"] = kwargs["task_intent"]
        captured["pause_controller"] = kwargs["pause_controller"]
        return FakeRunner()

    class UnexpectedPreOpen:
        def __init__(self, **_kwargs):
            raise AssertionError("legacy DrawArtifact must use the general runner's app discovery")

    runtime = stage_runtime.StageRuntime()
    monkeypatch.setattr(stage_runtime, "build_runner", fake_build_runner)
    monkeypatch.setattr(stage_runtime, "ToolSession", UnexpectedPreOpen)
    monkeypatch.setattr(runtime, "_evidence", lambda _context: registry)
    monkeypatch.setattr(runtime, "_store", lambda _context: object())

    result = runtime._execute(
        stage,
        contract,
        {
            "run_id": "legacy-draw-run",
            "runtime_state": {},
            "pause_controller": pause_controller,
            "on_runner": registered_runners.append,
        },
        threading.Event(),
        time.monotonic(),
    )

    profile = captured["profile"]
    assert result.ok is True
    assert captured["task"] == contract.task
    assert profile.stage_type == "ExecuteDesktopTask"
    assert {"click", "drag", "draw_strokes", "scroll"} <= set(
        profile.model_tool_allowlist
    )
    assert "execute_subgoal" not in profile.model_tool_allowlist
    assert captured["intent"].original_text == contract.task
    assert captured["pause_controller"] is pause_controller
    assert len(registered_runners) == 1
    assert isinstance(registered_runners[0], FakeRunner)
    record = registry.records()[-1]
    assert record["predicate"] == "artifact.semantic_visible"
    assert record["provider"] == "semantic_visual_judge"
    assert record["metadata"]["runtime_stage_type"] == "ExecuteDesktopTask"
    assert result.output["runtime_stage_type"] == "ExecuteDesktopTask"
    assert CompletionContractVerifier(contract, registry, None).verify()["ok"] is True


def test_typed_stage_guidance_is_not_part_of_visible_completion_task():
    from winpilot.agent.runtime_profile import build_runtime_profile

    semantic_goal = "In the already opened Drawing Harbor, draw a bird. The visible result must be clearly recognizable."
    guidance = "Prefer execute_subgoal with normalized_strokes in canvas-relative 0..1000 geometry."
    profile = build_runtime_profile(semantic_goal, stage_guidance=guidance)
    assert profile.stage_guidance == guidance
    assert guidance not in profile.prompt_section()
    assert profile.prompt_section() == ""


def test_adaptive_visual_profile_does_not_duplicate_scene_instructions():
    from winpilot.agent.runtime_profile import RuntimeProfile

    section = RuntimeProfile(adaptive_visual_interaction=True).prompt_section()

    assert section == ""


def test_desktop_schema_is_not_specialized_by_visual_output_vocabulary():
    from winpilot.agent.runtime_profile import build_runtime_profile
    from winpilot.agent.tool_schemas import tool_schemas

    contract, _graph = _compile(TASKS[0])
    stage = next(item for item in contract.stages if item.type.value == "ExecuteDesktopTask")
    profile = build_runtime_profile(
        "Draw the requested subject",
        task_contract=contract,
        stage=stage,
    )
    schemas = tool_schemas(
        planning=False,
        allow_shell=False,
        allow_files=False,
        profile="lean_gui",
        native_vision=True,
        required_capabilities=profile.required_capabilities,
        stage_tools=profile.stage_tools,
        stage_type=profile.stage_type,
        # Compatibility callers may still pass this obsolete flag.  It must
        # not specialize a general desktop stage.
        surface_output_mode=True,
    )
    legacy_stage_schemas = tool_schemas(
        planning=False,
        allow_shell=False,
        allow_files=False,
        profile="lean_gui",
        native_vision=True,
        required_capabilities=profile.required_capabilities,
        stage_tools=profile.stage_tools,
        stage_type="DrawArtifact",
        surface_output_mode=True,
    )
    assert legacy_stage_schemas == schemas
    execute = next(item for item in schemas if item["function"]["name"] == "execute_subgoal")
    parameters = execute["function"]["parameters"]
    assert parameters["required"] == ["subgoal"]
    assert {"input", "click", "clicks", "draw", "draws"} <= set(parameters["properties"])
    draw_item = parameters["properties"]["draws"]["items"]
    assert "description" not in parameters["properties"]["draws"]
    assert "required" not in draw_item
    assert "anyOf" not in draw_item
    assert draw_item["properties"]["box_mode"]["enum"] == [
        "endpoints", "xywh",
    ]
    assert "maxItems" not in parameters["properties"]["draws"]
    serialized = str(parameters).casefold()
    assert "shared silhouette" not in serialized
    assert "scene plan" not in serialized
    assert profile.adaptive_visual_interaction is True
    assert profile.surface_output_mode is False
    assert profile.inline_semantic_verification is False
    assert profile.model_tool_allowlist == ()


def test_deprecated_draw_profile_flags_do_not_install_a_special_runner_path():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.runtime_profile import RuntimeProfile

    runner = AgentRunner(
        "Draw a bird",
        runtime_profile=RuntimeProfile(
            inline_semantic_verification=True,
            stage_type="DrawArtifact",
            surface_output_mode=True,
            surface_output_criteria="legacy drawing criterion",
        ),
        max_steps_override=2,
    )
    try:
        assert not hasattr(runner, "_inline_stage_semantic_verdict")
        assert not hasattr(runner, "_inline_semantic_region")
        assert not hasattr(runner, "_surface_output_semantic_passed")
    finally:
        runner.session.close()


def test_native_vision_desktop_goal_exposes_artifact_and_gui_primitives():
    from winpilot.agent.tool_schemas import tool_schemas

    contract, _graph = _compile(TASKS[0])
    stage = contract.stages[0]
    selection = stage.execution
    assert selection is not None
    names = {
        item["function"]["name"]
        for item in tool_schemas(
            planning=False,
            coding_runtime=False,
            allow_shell=False,
            allow_files=False,
            profile="lean_gui",
            native_vision=True,
            required_capabilities=tuple(selection.required_capabilities),
            stage_tools=tuple(selection.tools),
        )
    }
    assert {
        "capture_artifact", "send_artifact", "verify_delivery",
        "paste_image_file", "read_text", "click", "type_text", "scroll",
        "drag", "draw_strokes", "mouse_down", "move_mouse", "mouse_up",
    } <= names


def test_observation_frame_cannot_be_used_as_sendable_artifact(tmp_path, monkeypatch):
    import numpy as np

    from winpilot.tasking.artifacts import ArtifactStore

    monkeypatch.setattr("winpilot.tasking.artifacts.capture", lambda _region=None: np.full((20, 30, 3), 255, dtype=np.uint8))
    store = ArtifactStore("run-observation", tmp_path)
    frame = store.capture_observation(scope="screen")
    assert frame.sendable is False
    assert frame.purpose == "observation"
    with pytest.raises(ValueError, match="cannot be sent"):
        store.get(frame.artifact_id, require_sendable=True)


def test_artifact_is_current_run_nonempty_hashed_and_source_bound(tmp_path, monkeypatch):
    import hashlib
    import numpy as np

    from winpilot.tasking.artifacts import ArtifactStore

    monkeypatch.setattr("winpilot.tasking.artifacts.capture", lambda _region=None: np.zeros((18, 22, 3), dtype=np.uint8))
    started = time.time()
    store = ArtifactStore("run-artifact", tmp_path)
    artifact = store.capture_artifact(scope="screen", source_window="Drawing Harbor")
    path = Path(artifact.managed_path)
    assert artifact.run_id == "run-artifact"
    assert path.is_file() and path.stat().st_size > 0
    assert artifact.created_at >= started
    assert artifact.source_window == "Drawing Harbor"
    assert artifact.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert store.get(artifact.artifact_id).sha256 == artifact.sha256


def test_artifact_manifest_restores_hash_verified_run_store(tmp_path):
    from winpilot.tasking.artifacts import ArtifactStore

    source = tmp_path / "source.txt"
    source.write_text("persisted artifact", encoding="utf-8")
    root = tmp_path / "managed"
    created = ArtifactStore("run-persisted", root).import_file(source)

    restored = ArtifactStore("run-persisted", root)
    artifact = restored.get(created.artifact_id)

    assert artifact.artifact_id == created.artifact_id
    assert artifact.sha256 == created.sha256
    assert restored.snapshot()["count"] == 1


def test_artifact_manifest_rejects_path_escape(tmp_path):
    import json

    from winpilot.tasking.artifacts import ArtifactStore

    root = tmp_path / "managed"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be exposed", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({
        "format": "artifact-store/1",
        "run_id": "run-secure",
        "artifacts": [{
            "artifact_id": "art-escaped",
            "run_id": "run-secure",
            "managed_path": str(outside),
            "sha256": "0" * 64,
            "mime_type": "text/plain",
            "size_bytes": outside.stat().st_size,
            "source_window": "",
            "created_at": time.time(),
            "purpose": "user_output",
            "sendable": True,
        }],
    }), encoding="utf-8")

    restored = ArtifactStore("run-secure", root)

    assert restored.snapshot()["count"] == 0
    with pytest.raises(KeyError):
        restored.get("art-escaped")


def test_artifact_get_rechecks_run_store_boundary(tmp_path):
    from winpilot.tasking.artifacts import ArtifactStore

    source = tmp_path / "source.txt"
    source.write_text("registered payload", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be exposed", encoding="utf-8")
    store = ArtifactStore("run-access-check", tmp_path / "managed")
    artifact = store.import_file(source)
    artifact.managed_path = str(outside)

    with pytest.raises(ValueError, match="escaped the run-scoped store"):
        store.get(artifact.artifact_id)


def test_explicit_artifact_source_never_falls_back_to_foreground(monkeypatch):
    from winpilot.tasking.stage_runtime import StageRuntime

    monkeypatch.setattr("winpilot.tasking.stage_runtime.win32.find_window", lambda _source: None)
    assert StageRuntime._resolve_hwnd("Drawing Harbor") == 0


def test_visual_match_recognizes_resized_same_artifact(tmp_path):
    from PIL import Image, ImageDraw

    from winpilot.tasking.artifacts import ArtifactStore

    source_path = tmp_path / "source.png"
    source = Image.new("RGB", (240, 160), "white")
    draw = ImageDraw.Draw(source)
    draw.ellipse((35, 25, 200, 140), outline="black", width=8)
    draw.line((45, 30, 70, 5, 95, 32), fill="black", width=7)
    draw.line((145, 32, 175, 5, 195, 35), fill="black", width=7)
    draw.ellipse((82, 72, 96, 86), fill="black")
    draw.ellipse((142, 72, 156, 86), fill="black")
    source.save(source_path)
    frame_path = tmp_path / "frame.png"
    frame = Image.new("RGB", (800, 600), "#ececec")
    frame.paste(source.resize((120, 80)), (510, 310))
    frame.save(frame_path)
    store = ArtifactStore("run-match", tmp_path / "managed")
    artifact = store.import_file(source_path)
    post_send = store.import_file(frame_path, purpose="observation", sendable=False)
    match = store.visual_match(artifact.artifact_id, post_send.artifact_id)
    assert match["matched"] is True, match


def test_visual_match_recognizes_small_attachment_confirmation_thumbnail(tmp_path):
    from PIL import Image, ImageDraw

    from winpilot.tasking.artifacts import ArtifactStore

    source_path = tmp_path / "source-small.png"
    source = Image.new("RGB", (240, 160), "white")
    draw = ImageDraw.Draw(source)
    draw.ellipse((25, 35, 205, 135), outline="black", width=8)
    draw.line((35, 110, 100, 55, 205, 110), fill="black", width=7)
    source.save(source_path)
    frame_path = tmp_path / "confirmation.png"
    frame = Image.new("RGB", (800, 600), "#222222")
    frame.paste(source.resize((48, 32)), (325, 225))
    frame.save(frame_path)
    store = ArtifactStore("run-small-preview", tmp_path / "managed-small")
    artifact = store.import_file(source_path)
    preview = store.import_file(frame_path, purpose="observation", sendable=False)
    match = store.visual_match(
        artifact.artifact_id,
        preview.artifact_id,
        target_region={"left": 250, "top": 150, "width": 300, "height": 250},
    )
    assert match["matched"] is True, match
    assert 0.04 <= match["template_scale"] <= 0.24


def test_frame_change_proves_only_a_changed_stage_owned_region(tmp_path):
    from PIL import Image, ImageDraw

    from winpilot.tasking.artifacts import ArtifactStore

    before_path = tmp_path / "before.png"
    Image.new("RGB", (800, 600), "#222222").save(before_path)
    after_path = tmp_path / "after.png"
    after = Image.open(before_path).copy()
    ImageDraw.Draw(after).rectangle((300, 160, 580, 470), fill="#dddddd")
    after.save(after_path)

    store = ArtifactStore("run-frame-change", tmp_path / "managed-change")
    before = store.import_file(before_path, purpose="observation", sendable=False)
    current = store.import_file(after_path, purpose="observation", sendable=False)
    changed = store.frame_change(
        before.artifact_id,
        current.artifact_id,
        target_region={"left": 240, "top": 120, "width": 400, "height": 400},
    )
    assert changed["changed"] is True, changed
    assert changed["change_ratio"] > 0.1
    unchanged = store.frame_change(
        before.artifact_id,
        current.artifact_id,
        target_region={"left": 0, "top": 0, "width": 150, "height": 100},
    )
    assert unchanged["changed"] is False, unchanged


def test_frame_change_accepts_coherent_remote_feedback_without_accepting_noise(
    tmp_path,
):
    import numpy as np
    from PIL import Image, ImageDraw

    from winpilot.tasking.artifacts import ArtifactStore

    before_path = tmp_path / "remote-before.png"
    Image.new("L", (1200, 800), 238).save(before_path)
    coherent_path = tmp_path / "remote-coherent.png"
    coherent = Image.open(before_path).copy()
    draw = ImageDraw.Draw(coherent)
    draw.rectangle((300, 610, 900, 650), outline=45, width=3)
    draw.rectangle((330, 622, 560, 640), fill=80)
    coherent.save(coherent_path)

    noise_path = tmp_path / "remote-noise.png"
    noisy = np.asarray(Image.open(before_path), dtype=np.uint8).copy()
    rng = np.random.default_rng(20260717)
    indices = rng.choice(noisy.size, size=2200, replace=False)
    noisy.flat[indices] = 45
    Image.fromarray(noisy).save(noise_path)

    store = ArtifactStore("run-coherent-change", tmp_path / "managed-coherent")
    before = store.import_file(before_path, purpose="observation", sendable=False)
    coherent_frame = store.import_file(
        coherent_path, purpose="observation", sendable=False,
    )
    noise_frame = store.import_file(
        noise_path, purpose="observation", sendable=False,
    )
    region = {"left": 0, "top": 0, "width": 1200, "height": 800}
    coherent_change = store.frame_change(
        before.artifact_id,
        coherent_frame.artifact_id,
        target_region=region,
        min_change_ratio=0.001,
        min_mean_delta=0.45,
        allow_coherent_region=True,
    )
    assert coherent_change["changed"] is True, coherent_change
    assert coherent_change["coherent_change"]["detected"] is True

    scattered_noise = store.frame_change(
        before.artifact_id,
        noise_frame.artifact_id,
        target_region=region,
        min_change_ratio=0.001,
        min_mean_delta=0.45,
        allow_coherent_region=True,
    )
    assert scattered_noise["change_ratio"] >= 0.001
    assert scattered_noise["coherent_change"]["detected"] is False
    assert scattered_noise["changed"] is False, scattered_noise


def test_completion_rejects_preview_without_full_desktop_goal_proof(tmp_path, monkeypatch):
    import numpy as np

    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.completion import CompletionContractVerifier
    from winpilot.tasking.evidence import EvidenceRegistry

    contract, _graph = _compile(TASKS[0])
    monkeypatch.setattr("winpilot.tasking.artifacts.capture", lambda _region=None: np.zeros((16, 16, 3), dtype=np.uint8))
    store = ArtifactStore("run-preview", tmp_path)
    artifact = store.capture_artifact(scope="screen", source_window="Drawing Harbor")
    evidence = EvidenceRegistry("run-preview")
    evidence.record(
        "artifact.composer_preview",
        provider="qq_composer_preview",
        verified=True,
        artifact_id=artifact.artifact_id,
        recipient="我的手机",
        metadata={"delivery": False},
    )
    verdict = CompletionContractVerifier(contract, evidence, store).verify()
    assert verdict["ok"] is False
    assert {item["obligation_id"] for item in verdict["missing"]} == {
        "desktop.full_goal_verified",
    }


def test_delivery_sub_evidence_cannot_replace_full_desktop_goal_proof(tmp_path, monkeypatch):
    import numpy as np

    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.completion import CompletionContractVerifier
    from winpilot.tasking.evidence import EvidenceRegistry

    contract, _graph = _compile(TASKS[0])
    monkeypatch.setattr("winpilot.tasking.artifacts.capture", lambda _region=None: np.zeros((16, 16, 3), dtype=np.uint8))
    store = ArtifactStore("run-identity", tmp_path)
    primary = store.capture_artifact(scope="screen", source_window="Drawing Harbor")
    unrelated = store.capture_artifact(scope="screen", source_window="Drawing Harbor")
    evidence = EvidenceRegistry("run-identity")
    evidence.record("artifact.current_run_valid", provider="artifact_store_capture", verified=True, artifact_id=primary.artifact_id)
    evidence.record("conversation.correct_recipient", provider="communication_adapter_context", verified=True, recipient="我的手机")
    evidence.record("delivery.real_send_action", provider="communication_adapter_send", verified=True, artifact_id=unrelated.artifact_id, recipient="我的手机")
    evidence.record("delivery.visible_outbound", provider="communication_adapter_visible_outbound", verified=True, artifact_id=unrelated.artifact_id, recipient="我的手机")
    verdict = CompletionContractVerifier(contract, evidence, store).verify()
    assert verdict["ok"] is False
    assert {item["obligation_id"] for item in verdict["missing"]} == {
        "desktop.full_goal_verified",
    }


@pytest.mark.parametrize(
    ("predicate", "stage_id"),
    [
        ("unrelated.verified_claim", "expected-stage"),
        ("stage.verified_outcome", "different-stage"),
    ],
)
def test_completion_requires_exact_predicate_and_stage(predicate, stage_id):
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.completion import CompletionContractVerifier
    from winpilot.tasking.evidence import EvidenceRegistry

    contract = TaskContractCompiler().compile("Organize the currently visible content")
    obligation = contract.obligations[0]
    evidence = EvidenceRegistry("run-exact-evidence")
    evidence.record(
        predicate,
        provider=obligation.evidence_providers[0].id,
        verified=True,
        stage_id=obligation.stage_id if stage_id == "expected-stage" else stage_id,
    )

    verdict = CompletionContractVerifier(contract, evidence, None).verify()

    assert verdict["ok"] is False
    assert verdict["missing"][0]["obligation_id"] == obligation.id
    assert verdict["missing"][0]["predicate"] == obligation.evidence_predicate


def test_completion_accepts_exact_predicate_stage_and_provider():
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.completion import CompletionContractVerifier
    from winpilot.tasking.evidence import EvidenceRegistry

    contract = TaskContractCompiler().compile("Organize the currently visible content")
    obligation = contract.obligations[0]
    evidence = EvidenceRegistry("run-exact-evidence")
    evidence.record(
        obligation.evidence_predicate,
        provider=obligation.evidence_providers[0].id,
        verified=True,
        stage_id=obligation.stage_id,
    )

    verdict = CompletionContractVerifier(contract, evidence, None).verify()

    assert verdict["ok"] is True
    assert verdict["final_conditions"] == [{
        "condition": obligation.id,
        "satisfied": True,
        "evidence_id": verdict["obligations"][0]["evidence_id"],
    }]


def test_completion_rejects_contract_with_stage_obligations_removed_from_final_contract():
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.completion import CompletionContractVerifier
    from winpilot.tasking.evidence import EvidenceRegistry

    compiled = TaskContractCompiler().compile("Organize the currently visible content")
    malformed = compiled.model_copy(update={"obligations": [], "final_conditions": []})

    verdict = CompletionContractVerifier(
        malformed,
        EvidenceRegistry("run-malformed-contract"),
        None,
    ).verify()

    assert verdict["ok"] is False
    assert "no completion obligations" in verdict["reason"]
    assert "do not match" in verdict["reason"]


def test_arp_end_rejects_arbitrary_verified_evidence_for_task_contract():
    from winpilot.orchestrator.contracts import ArpNodeKind
    from winpilot.orchestrator.runtime import ArpExecution, NodeResult
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.evidence import EvidenceRegistry

    contract, graph = TaskContractCompiler().compile_graph(
        "Open Calculator",
        cold_start=True,
    )
    obligation = contract.obligations[0]

    def stage_handler(_node, context, _stop_event):
        registry = EvidenceRegistry(context["run_id"])
        context["runtime_state"]["evidence_registry"] = registry
        record = registry.record(
            "unrelated.verified_claim",
            provider=obligation.evidence_providers[0].id,
            verified=True,
            stage_id=obligation.stage_id,
        )
        return NodeResult(evidence=[record])

    execution = ArpExecution(
        graph,
        {ArpNodeKind.TASK_STAGE: stage_handler},
        run_id="arp-contract-fail-closed",
    )
    result = execution.run()

    assert result["status"] == "failed"
    assert "missing task-contract evidence" in result["error"]
    assert result["completion_contract"]["ok"] is False
    assert result["completion_contract"]["missing"][0]["predicate"] == "application.open"


def test_arp_end_marks_done_only_after_exact_contract_completion():
    from winpilot.orchestrator.contracts import ArpNodeKind
    from winpilot.orchestrator.runtime import ArpExecution, NodeResult
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.evidence import EvidenceRegistry

    contract, graph = TaskContractCompiler().compile_graph(
        "Open Calculator",
        cold_start=True,
    )
    obligation = contract.obligations[0]

    def stage_handler(_node, context, _stop_event):
        registry = EvidenceRegistry(context["run_id"])
        context["runtime_state"]["evidence_registry"] = registry
        record = registry.record(
            obligation.evidence_predicate,
            provider=obligation.evidence_providers[0].id,
            verified=True,
            stage_id=obligation.stage_id,
        )
        return NodeResult(evidence=[record])

    result = ArpExecution(
        graph,
        {ArpNodeKind.TASK_STAGE: stage_handler},
        run_id="arp-contract-complete",
    ).run()

    assert result["status"] == "done"
    assert result["completion_contract"]["ok"] is True
    assert result["outputs"]["end"]["final_conditions"][0]["satisfied"] is True
    assert result["contract_evidence"]["records"][-1]["predicate"] == "contract.complete"


def test_capture_scope_distinguishes_canvas_from_explicit_window():
    first, _ = _compile(TASKS[0])
    third, _ = _compile(TASKS[2])
    assert first.intent.capture_scope == "canvas"
    assert third.intent.capture_scope == "window"
    assert first.stages[0].parameters["goal"] == TASKS[0]
    assert third.stages[0].parameters["goal"] == TASKS[2]


def test_stage_skill_graph_resumes_at_failed_stage_instead_of_whole_task():
    contract, graph = _compile(TASKS[0])
    surface = next(
        node for node in graph.nodes
        if node.kind.value == "task_stage"
        and node.config["stage"]["type"] == "ExecuteDesktopTask"
    )
    skill = next(
        node for node in graph.nodes
        if node.kind.value == "skill_replay"
        and node.config.get("stage_id") == surface.id
    )
    routes = {(edge.branch, edge.target) for edge in graph.edges if edge.source == skill.id}
    assert ("miss", surface.id) in routes
    assert ("fail", surface.id) in routes
    assert skill.config["resume_on_failure"] == surface.id
    assert skill.config["parameters"]["goal"] == contract.task
    assert "subject" not in skill.config["parameters"]


def test_stage_skill_matching_checks_type_parameters_and_app_version(monkeypatch, tmp_path):
    from winpilot.memory import experience

    script_name = "draw.wps.json"
    (tmp_path / script_name).write_text('{"version": "wps/1", "steps": []}', encoding="utf-8")
    monkeypatch.setattr(experience.config, "SCRIPTS_DIR", tmp_path)

    node = {
        "id": "skill-1", "kind": "skill", "status": "active", "confidence": 0.8,
        "title": "drawing stage", "payload": {
            "script": script_name, "script_sha256": experience.script_sha256(script_name),
            "verification_state": "source_run_verified",
            "stage_type": "DrawArtifact",
            "parameters": {"app": "Drawing Harbor"}, "outputs": [{"name": "visual_content"}],
            "verification": ["artifact.semantic_visible"], "app_version": "11.0",
            "provisional": False, "successes": 2,
        },
    }

    class FakeStore:
        def list_nodes(self, **_kwargs):
            return [node]

    monkeypatch.setattr(experience, "_store", lambda: FakeStore())
    monkeypatch.setattr(experience, "enabled", lambda: True)
    script, matched = experience.reusable_stage_script_for(
        "DrawArtifact", {"app": "Drawing Harbor", "subject": "bird"}, app_version="11.0",
    )
    assert script == script_name
    assert matched and matched["id"] == "skill-1"
    assert experience.reusable_stage_script_for(
        "DrawArtifact", {"app": "Canvas Harbor"}, app_version="11.0",
    )[0] == ""
    assert experience.reusable_stage_script_for(
        "DrawArtifact", {"app": "Drawing Harbor"}, app_version="12.0",
    )[0] == ""


def test_server_upgrades_legacy_frontend_goal_graph(monkeypatch):
    from winpilot.orchestrator.contracts import ArpEdge, ArpGraph, ArpNode, ArpNodeKind
    from winpilot.orchestrator.graph_compiler import legacy_goal_graph_task

    graph = ArpGraph(
        id="legacy", name="legacy",
        nodes=[
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="goal", kind=ArpNodeKind.AGENT_GOAL, config={"goal": TASKS[0], "max_steps": 55}),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        edges=[ArpEdge(id="a", source="start", target="goal"), ArpEdge(id="b", source="goal", target="end")],
        metadata={"source": "agent-cockpit"},
    )
    assert legacy_goal_graph_task(graph) == (TASKS[0], 55)


def test_communication_registry_requires_explicit_plugin_registration(
    monkeypatch,
    tmp_path,
):
    import json

    from winpilot.agent.tools import ToolSession
    from winpilot.tasking.adapters import CommunicationAdapter, DomainAdapterRegistry
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry

    monkeypatch.setattr(DomainAdapterRegistry, "_types", {})
    monkeypatch.setattr(DomainAdapterRegistry, "_channel_names", {})

    assert DomainAdapterRegistry.registered_adapters() == []
    assert DomainAdapterRegistry.registered_channels() == []
    assert DomainAdapterRegistry.probe(channel="Imaginary Chat")["registered"] is False

    calls: list[tuple[str, str]] = []

    class ImaginaryProvider(CommunicationAdapter):
        id = "imaginary"
        app_name = "Imaginary Chat"
        channels = ("imaginary-chat",)

        def open_conversation(self, recipient: str, *, stage_id: str = ""):
            self.recipient = recipient
            calls.append(("open", recipient))
            return {"ok": True, "recipient": recipient, "target_hwnd": 123}

        def resolve_composer(self):
            calls.append(("composer", self.recipient))
            return {"ok": True, "target_hwnd": 123}

        def paste_or_attach_artifact(self, artifact_id: str, *, stage_id: str = ""):
            calls.append(("stage", artifact_id))
            return {"ok": True, "artifact_id": artifact_id}

        def send_current_composer(self, artifact_id: str = "", *, stage_id: str = ""):
            calls.append(("send", artifact_id))
            return {"ok": True, "artifact_id": artifact_id}

        def send_text(self, text: str, *, stage_id: str = ""):
            calls.append(("text", text))
            return {"ok": True, "text": text}

        def verify_latest_outbound(
            self,
            *,
            artifact_id: str = "",
            text: str = "",
            stage_id: str = "",
        ):
            calls.append(("verify", artifact_id or text))
            return {"ok": True, "artifact_id": artifact_id, "text": text}

    DomainAdapterRegistry.register(
        ImaginaryProvider,
        channels=("Imaginary Chat", "IM"),
    )
    assert DomainAdapterRegistry.component_for_channel("im") == "imaginary_adapter"
    probe = DomainAdapterRegistry.probe(
        channel="Imaginary Chat",
        component="imaginary_adapter",
    )
    assert probe["registered"] is True
    assert probe["probe_scope"] == "static_registry_only"
    descriptors = DomainAdapterRegistry.registered_adapters()
    assert len(descriptors) == 1
    assert descriptors[0]["adapter_id"] == "imaginary"
    assert descriptors[0]["app_name"] == "Imaginary Chat"
    assert descriptors[0]["component"] == "imaginary_adapter"
    assert set(descriptors[0]["channels"]) == {
        "imaginary", "imaginary-chat", "Imaginary Chat", "IM",
    }
    assert descriptors[0]["auto_discover"] is False

    store = ArtifactStore("run-plugin", tmp_path / "artifacts")
    evidence = EvidenceRegistry("run-plugin")
    provider = DomainAdapterRegistry.create(
        "IM",
        run_id="run-plugin",
        task="send through a configured provider",
        store=store,
        evidence=evidence,
    )
    assert provider.store is store
    assert provider.evidence is evidence
    assert provider.open_conversation("Test Contact")["ok"] is True
    assert provider.resolve_composer()["ok"] is True
    assert calls == [("open", "Test Contact"), ("composer", "Test Contact")]

    session = ToolSession(task="send through an explicitly configured provider")
    session.run_id = "run-plugin"
    session._managed_artifacts = lambda: (store, evidence)
    try:
        opened = json.loads(session._t_open_conversation({
            "app": "IM",
            "recipient": "Another Contact",
        }).text)
    finally:
        session.close()
    assert opened["ok"] is True
    assert opened["verified"] is True
    assert opened["target_hwnd"] == 123
    assert session.hwnd == 123
    assert calls[-2:] == [
        ("open", "Another Contact"),
        ("composer", "Another Contact"),
    ]

    assert DomainAdapterRegistry.unregister("imaginary_adapter") is True
    assert DomainAdapterRegistry.registered_adapters() == []
    assert DomainAdapterRegistry.probe(channel="IM")["registered"] is False
    with pytest.raises(KeyError, match="no communication provider"):
        DomainAdapterRegistry.create(
            "IM",
            run_id="run-plugin",
            task="send",
            store=store,
            evidence=evidence,
        )


def test_desktop_communication_tools_report_provider_unavailable_without_hidden_fallback(
    monkeypatch,
):
    import json

    from winpilot.agent.tools import ToolSession
    from winpilot.tasking.adapters import DomainAdapterRegistry

    monkeypatch.setattr(DomainAdapterRegistry, "_types", {})
    monkeypatch.setattr(DomainAdapterRegistry, "_channel_names", {})
    session = ToolSession(task="send a message through the visible desktop client")
    try:
        unresolved = json.loads(session._t_open_conversation({
            "recipient": "Test Contact",
        }).text)
        explicit_unknown = json.loads(session._t_open_conversation({
            "app": "Unknown Chat",
            "recipient": "Test Contact",
        }).text)
    finally:
        session.close()

    for payload in (unresolved, explicit_unknown):
        assert payload["ok"] is False
        assert payload["verified"] is False
        assert payload["provider_available"] is False
        assert payload["failure_type"] == "provider_unavailable"
        assert "fast_path_advice" not in payload


def test_client_desktop_task_does_not_require_a_communication_provider(
    monkeypatch,
):
    from winpilot.tasking.adapters import DomainAdapterRegistry
    from winpilot.tasking.compiler import TaskContractCompiler

    monkeypatch.setattr(DomainAdapterRegistry, "_types", {})
    monkeypatch.setattr(DomainAdapterRegistry, "_channel_names", {})
    contract, _graph = TaskContractCompiler().compile_graph(
        "In the visible desktop client, send hello to Test Contact.",
        require_gui=True,
        cold_start=True,
    )

    assert [stage.type.value for stage in contract.stages] == [
        "ExecuteDesktopTask",
    ]
    assert contract.satisfiable is True
    assert contract.missing_capabilities == []
    execution = contract.stages[0].execution
    assert execution is not None
    assert execution.adapter == ""
    assert {
        "open_conversation", "send_text", "send_artifact", "verify_delivery",
    }.isdisjoint(execution.model_tools)
    assert not any(
        capability.startswith((
            "conversation.", "recipient.", "communication.", "delivery.",
        ))
        for capability in execution.required_capabilities
    )
