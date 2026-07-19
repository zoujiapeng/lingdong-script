from __future__ import annotations

import pytest


INTERACTIVE_TASKS = (
    "和我在星图工坊应用上玩游戏，你看看星图工坊就知道了，我已经开始了",
    "跟我互动；在像素庭院应用中观察当前界面，我先操作过了",
    "轮到你了，请根据当前浏览器窗口完成你的下一步",
    "Collaborate with me in the current browser window; I already started.",
)


def _assert_single_visual_model_stage(contract, task: str):
    assert contract.session is None
    assert contract.clarification_questions == []
    assert [stage.type.value for stage in contract.stages] == [
        "ExecuteDesktopTask",
    ]
    stage = contract.stages[0]
    assert stage.parameters["goal"] == task
    assert stage.parameters["original_task"] == task
    assert stage.parameters["preserve_original_intent"] is True
    assert [item.id for item in stage.obligations] == [
        "desktop.full_goal_verified",
    ]
    assert {
        "observe", "click", "drag", "scroll", "done",
    } <= set(stage.execution.model_tools)
    return stage


@pytest.mark.parametrize(
    "task,expected_modality,expected_signal",
    (
        ("点击继续", "click", "imperative_pointer_relation"),
        ("请点按标为继续的项目", "click", "imperative_pointer_relation"),
        ("点继续", "click", "imperative_pointer_relation"),
        ("请在当前页面点击继续", "click", "imperative_pointer_relation"),
        ("Click Continue", "click", "imperative_pointer_relation"),
        ("Could you press the item marked Continue", "click", "imperative_pointer_relation"),
        ("把滑块拖到右边", "drag", "spatial_transfer_relation"),
        ("将可移动对象拖入右侧区域", "drag", "spatial_transfer_relation"),
        ("可移动对象向右拖", "drag", "spatial_transfer_relation"),
        ("往右拖一下这个可移动对象", "drag", "spatial_transfer_relation"),
        ("向右拖动可见对象", "drag", "spatial_transfer_relation"),
        ("Drag the movable item to the right", "drag", "spatial_transfer_relation"),
        ("Please drag this item into the region on the right", "drag", "spatial_transfer_relation"),
        ("Move this right", "infer_from_scene", "spatial_transfer_relation"),
        ("把蓝块放入目标区域", "infer_from_scene", "spatial_transfer_relation"),
        ("Place the blue object inside the destination region", "infer_from_scene", "spatial_transfer_relation"),
        ("在当前页面选择第二项", "infer_from_scene", "contextual_manipulation_relation"),
        ("On the visible page, select the second item", "infer_from_scene", "contextual_manipulation_relation"),
        ("在当前页面完成可见的交互任务", "infer_from_scene", "contextual_visual_goal_relation"),
        ("Solve the visible interaction on this page", "infer_from_scene", "contextual_visual_goal_relation"),
        ("关闭当前界面中挡住内容的项目", "infer_from_scene", "contextual_visual_goal_relation"),
    ),
)
def test_short_visual_commands_reach_bounded_typed_interaction(
    task: str,
    expected_modality: str,
    expected_signal: str,
):
    from winpilot.tasking.compiler import TaskContractCompiler

    contract, graph = TaskContractCompiler().compile_graph(task)
    interaction = contract.intent.interaction
    assert interaction.active is True
    assert interaction.mode == "direct_visual_manipulation"
    assert interaction.session_mode == "bounded"
    assert interaction.turn_owner == "agent"
    assert expected_modality in interaction.action_modalities
    expected_primary = (
        expected_modality
        if expected_modality in {"click", "drag", "draw", "type"}
        else "infer_from_scene"
    )
    assert interaction.primary_action_modality == expected_primary
    assert expected_signal in interaction.signals
    assert interaction.provenance["active"] == "typed_command_relation"
    _assert_single_visual_model_stage(contract, task)
    assert all(node.kind.value != "agent_goal" for node in graph.nodes)


def test_equivalent_direct_manipulation_rewrites_have_the_same_contract_shape():
    from winpilot.tasking.compiler import TaskContractCompiler

    rewrites = (
        "把可移动对象拖到右侧区域",
        "将可移动对象拖入右边的区域",
        "可移动对象向右拖",
        "Drag the movable object into the region on the right",
    )
    contracts = [TaskContractCompiler().compile(task) for task in rewrites]
    assert all(item.intent.interaction.action_modalities == ["drag"] for item in contracts)
    assert all(item.intent.interaction.mode == "direct_visual_manipulation" for item in contracts)
    assert all(item.intent.interaction.session_mode == "bounded" for item in contracts)
    for task, contract in zip(rewrites, contracts, strict=True):
        _assert_single_visual_model_stage(contract, task)


@pytest.mark.parametrize(
    "task",
    (
        "解释‘点击继续’这句话的意思",
        "统计这个页面的按钮点击率",
        "这份文档讨论如何把对象拖到右边",
        "The article explains how to drag an item into a region.",
        "Please write an article about clicking interface elements.",
        "Summarize the reported click rates on the current page.",
    ),
)
def test_action_words_in_descriptive_text_do_not_route_to_visual_interaction(task: str):
    from winpilot.tasking.compiler import TaskContractCompiler

    contract, graph = TaskContractCompiler().compile_graph(task)
    assert contract.intent.interaction.active is False
    assert "interact" not in contract.intent.operation
    assert contract.session is None
    expected_stage = (
        "CreateOrEditArtifact"
        if contract.intent.domain == "desktop"
        else "ExecuteSemanticTask"
    )
    assert [stage.type.value for stage in contract.stages] == [expected_stage]
    assert all(node.kind.value != "agent_goal" for node in graph.nodes)


@pytest.mark.parametrize(
    "task",
    (
        "请把会议移动到周五",
        "轮到你写下一段了",
        "等我先把报告写完",
        "拖一下行李箱",
        "按下电源键",
    ),
)
def test_non_visual_action_homonyms_do_not_become_gui_sessions(task: str):
    from winpilot.tasking.compiler import TaskContractCompiler

    contract, _graph = TaskContractCompiler().compile_graph(task)
    assert contract.intent.interaction.active is False
    assert contract.session is None
    assert "interact" not in contract.intent.operation


def test_high_confidence_semantic_extractor_can_veto_structural_candidate():
    from winpilot.tasking.intent import TaskIntentParser

    parser = TaskIntentParser(semantic_extractor=lambda _task: {
        "interaction": {"active": False, "confidence": 0.94},
    })
    intent = parser.parse("点击当前页面中的可见项目")

    assert intent.interaction.active is False
    assert "semantic_interaction_veto" in intent.interaction.signals
    assert "interact" not in intent.operation


def test_shared_visual_interaction_uses_one_visual_model_stage():
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(INTERACTIVE_TASKS[0])
    assert contract.intent.operation == ["interact"]
    assert contract.intent.source == "星图工坊"
    assert contract.intent.interaction.active is True
    assert contract.intent.interaction.mode == "turn_based"
    assert contract.intent.interaction.turn_owner == "agent"
    _assert_single_visual_model_stage(contract, INTERACTIVE_TASKS[0])
    assert contract.satisfiable is True


@pytest.mark.parametrize(
    "task",
    (
        "跟我玩游戏，现在到你了",
        "和我玩，接下来轮到你了",
        "陪我互动，这一轮该你了",
        "Play with me; now it is your turn.",
    ),
)
def test_minimal_shared_turn_handoff_observes_scene_before_acting(task: str):
    """A live shared turn must not require the user to restate visible rules."""
    from winpilot.tasking.compiler import TaskContractCompiler

    contract, graph = TaskContractCompiler().compile_graph(task, cold_start=True)

    assert contract.intent.interaction.active is True
    assert contract.intent.interaction.session_mode == "persistent"
    assert contract.intent.interaction.turn_owner == "agent"
    assert contract.intent.interaction.primary_action_modality == "infer_from_scene"
    _assert_single_visual_model_stage(contract, task)
    assert all(node.kind.value not in {"agent_goal", "skill_replay"} for node in graph.nodes)


@pytest.mark.parametrize(
    "task",
    (
        "轮到你写下一段了",
        "这篇文章写到你了解的部分",
        "把文件发到你了解的邮箱里",
    ),
)
def test_turn_like_prose_without_complete_shared_session_does_not_activate(task: str):
    from winpilot.tasking.intent import TaskIntentParser

    intent = TaskIntentParser().parse(task)

    assert intent.interaction.active is False


def test_actor_assignments_keep_role_ownership_instead_of_flattening_values():
    from winpilot.tasking.intent import TaskIntentParser

    intent = TaskIntentParser().parse(
        "和我在星图工坊应用上玩游戏，你看看星图工坊就知道了，"
        "我已经开始了，你用红色我用黑色"
    )
    assert intent.interaction.active is True
    assert intent.interaction.role_assignments == {
        "agent": {"assignment": "红色"},
        "user": {"assignment": "黑色"},
    }
    actors = {actor.id: actor for actor in intent.interaction.actors}
    assert actors["agent"].attributes == {"assignment": "红色"}
    assert actors["user"].attributes == {"assignment": "黑色"}


def test_interaction_routing_requires_a_live_relation_or_pointer_plus_visual_target():
    from winpilot.tasking.intent import TaskIntentParser

    parser = TaskIntentParser()
    for task in (
        "打开一个游戏",
        "在星图工坊应用上画一只猫",
    ):
        intent = parser.parse(task)
        assert intent.interaction.active is False, task
        assert "interact" not in intent.operation

    for task in (
        "在浏览器里拖动滑块到右侧",
        "点击验证按钮",
        "drag the visible card into the target area",
    ):
        intent = parser.parse(task)
        assert intent.interaction.active is True, task
        assert intent.interaction.mode == "direct_visual_manipulation"
        assert intent.interaction.session_mode == "bounded"
        assert intent.interaction.turn_owner == "agent"
        assert "interact" in intent.operation


def test_interaction_detection_generalizes_across_apps_languages_and_actions():
    from winpilot.tasking.intent import TaskIntentParser

    parser = TaskIntentParser()
    intents = [parser.parse(task) for task in INTERACTIVE_TASKS]
    assert all(item.interaction.active for item in intents)
    assert all("interact" in item.operation for item in intents)
    assert intents[2].source == "浏览器"
    assert intents[3].interaction.turn_owner == "agent"


def test_semantic_extractor_can_supply_interaction_frame_without_keyword_routing():
    from winpilot.tasking.intent import TaskIntentParser

    parser = TaskIntentParser(semantic_extractor=lambda _task: {
        "interaction": {
            "active": True,
            "confidence": 0.91,
            "mode": "continuous_cooperation",
            "turn_owner": "agent",
            "target": "active_window",
            "action_modalities": ["drag"],
        },
    })
    intent = parser.parse("Handle the evolving shared state now")
    assert intent.interaction.active is True
    assert intent.interaction.mode == "continuous_cooperation"
    assert intent.interaction.action_modalities == ["drag"]
    assert intent.interaction.primary_action_modality == "drag"
    assert "semantic_interaction_frame" in intent.interaction.signals


def test_new_interaction_contract_does_not_precompute_ephemeral_protocol_state():
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(INTERACTIVE_TASKS[0])
    stage = _assert_single_visual_model_stage(contract, INTERACTIVE_TASKS[0])
    assert stage.outputs == []
    assert stage.state_outputs == []


def test_legacy_interaction_contracts_default_new_constraints_without_rewrite():
    from winpilot.tasking.contracts import (
        InteractionIntent,
        InteractionSessionContract,
    )

    intent = InteractionIntent.model_validate({
        "active": True,
        "action_modalities": ["drag"],
    })
    session = InteractionSessionContract.model_validate({
        "session_id": "legacy-session",
        "mode": "direct_visual_manipulation",
    })

    assert intent.primary_action_modality == "infer_from_scene"
    assert session.primary_action_modality == "infer_from_scene"
    assert session.surface_region_ref == "surface_region"


def test_every_interaction_verifier_requirement_is_exposed():
    from winpilot.tasking.capability import CapabilityPlanner
    from winpilot.tasking.compiler import TaskContractCompiler

    for task in INTERACTIVE_TASKS:
        contract = TaskContractCompiler().compile(task)
        CapabilityPlanner.assert_satisfiable(contract)
        for stage in contract.stages:
            assert stage.execution is not None
            selection = stage.execution
            needed = set(selection.required_capabilities) | set(selection.verifier_requirements)
            assert needed <= set(selection.exposed_capabilities), stage.id
            assert selection.missing_capabilities == []


def test_fallback_semantic_stage_preserves_identified_source():
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile("在星图工坊应用中把已有内容润色一下")
    assert [stage.type.value for stage in contract.stages] == ["CreateOrEditArtifact"]
    assert contract.intent.source == "星图工坊"
    assert contract.stages[0].parameters["app"] == "星图工坊"
    assert contract.stages[0].parameters["source"] == "星图工坊"


def test_interaction_compiles_to_graph_with_one_visual_model_stage():
    from winpilot.tasking.compiler import TaskContractCompiler

    contract, graph = TaskContractCompiler().compile_graph(INTERACTIVE_TASKS[0])
    _assert_single_visual_model_stage(contract, INTERACTIVE_TASKS[0])
    assert all(node.kind.value != "agent_goal" for node in graph.nodes)
    assert [
        node.config["stage"]["type"]
        for node in graph.nodes
        if node.kind.value == "task_stage"
    ] == [stage.type.value for stage in contract.stages]


def test_external_turn_tactics_are_left_to_the_visual_model():
    from winpilot.tasking.compiler import TaskContractCompiler

    task = "轮到我了，等我操作后你再根据当前界面继续"
    contract = TaskContractCompiler().compile(task)
    assert contract.intent.interaction.active is True
    assert contract.intent.interaction.turn_owner == "user"
    stage = _assert_single_visual_model_stage(contract, task)
    assert "turn_guard" not in stage.parameters
    assert "on_external_turn" not in stage.parameters


def test_explicit_drag_uses_general_visual_model_and_full_goal_verifier():
    from winpilot.tasking.compiler import TaskContractCompiler

    task = (
        "In the current visible window, drag the movable disk through the "
        "visible route into the destination."
    )
    contract = TaskContractCompiler().compile(task, cold_start=True)
    stage = _assert_single_visual_model_stage(contract, task)
    assert stage.parameters["verification_policy"] == "full_goal_coverage"
    assert stage.execution.selected_evidence_providers == {
        "desktop.full_goal_verified": "desktop_closed_loop_verifier",
    }
    assert "visual.semantic_verify" in stage.execution.verifier_requirements
    assert "drag" in stage.execution.model_tools


def test_unknown_or_click_interaction_uses_same_general_completion_provider():
    from winpilot.tasking.compiler import TaskContractCompiler

    task = (
        "In the current visible window, click the only control matching the "
        "visible instruction."
    )
    contract = TaskContractCompiler().compile(task, cold_start=True)
    stage = _assert_single_visual_model_stage(contract, task)
    assert stage.execution.selected_evidence_providers == {
        "desktop.full_goal_verified": "desktop_closed_loop_verifier",
    }
    assert "visual.semantic_verify" in stage.execution.verifier_requirements
    assert "click" in stage.execution.model_tools
