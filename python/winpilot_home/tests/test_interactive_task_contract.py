from __future__ import annotations

import pytest


INTERACTIVE_TASKS = (
    "和我在画板上玩游戏，你看看画板就知道了，我已经开始了",
    "跟我在画图中互动；观察当前界面，我先操作过了",
    "轮到你了，请根据当前浏览器窗口完成你的下一步",
    "Collaborate with me in the current browser window; I already started.",
)


BOUNDED_STAGE_TYPES = [
    "ObserveScene",
    "InferInteraction",
    "PerformTurn",
    "VerifyLocalEffect",
]


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
    assert contract.session is not None
    assert contract.session.target == "active_window"
    assert contract.session.primary_action_modality == expected_primary
    assert [stage.type.value for stage in contract.stages] == BOUNDED_STAGE_TYPES
    assert all(node.kind.value != "agent_goal" for node in graph.nodes)
    infer = next(stage for stage in contract.stages if stage.type.value == "InferInteraction")
    assert expected_signal in infer.parameters["intent_signals"]


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
    assert all([stage.type.value for stage in item.stages] == BOUNDED_STAGE_TYPES for item in contracts)


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
    assert [stage.type.value for stage in contract.stages] == ["CreateOrEditArtifact"]
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


def test_shared_visual_interaction_uses_typed_session_stages():
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(INTERACTIVE_TASKS[0])
    assert contract.intent.operation == ["interact"]
    assert contract.intent.source == "Paint"
    assert contract.intent.interaction.active is True
    assert contract.intent.interaction.mode == "turn_based"
    assert contract.intent.interaction.turn_owner == "agent"
    assert contract.session is not None
    assert contract.session.target == "Paint"
    assert contract.session.max_agent_turns_per_activation == 1
    assert contract.session.wait_for_external_change is True
    assert [stage.type.value for stage in contract.stages] == [
        "ObserveScene",
        "InferInteraction",
        "PerformTurn",
        "VerifyLocalEffect",
        "WaitForExternalChange",
    ]
    assert [stage.parameters["max_steps"] for stage in contract.stages] == [2, 3, 4, 2, 1]
    assert contract.satisfiable is True


def test_actor_assignments_keep_role_ownership_instead_of_flattening_values():
    from winpilot.tasking.intent import TaskIntentParser

    intent = TaskIntentParser().parse(
        "和我在画板上玩游戏，你看看画板就知道了，我已经开始了，你用红色我用黑色"
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
        "在画板上画一只猫",
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


def test_interaction_frames_are_ephemeral_state_not_sendable_artifacts():
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(INTERACTIVE_TASKS[0])
    observe = contract.stages[0]
    assert observe.outputs == []
    assert {item.state_type for item in observe.state_outputs} == {
        "authoritative_visual_frame", "bound_interaction_surface",
    }
    assert all(item.ephemeral for item in observe.state_outputs)
    infer = next(
        stage for stage in contract.stages
        if stage.type.value == "InferInteraction"
    )
    assert {item.state_type for item in infer.state_outputs} == {
        "interaction_protocol", "per_turn_interaction_region",
    }


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

    contract = TaskContractCompiler().compile("在画板上把已有内容润色一下")
    assert [stage.type.value for stage in contract.stages] == ["CreateOrEditArtifact"]
    assert contract.intent.source == "Paint"
    assert contract.stages[0].parameters["app"] == "Paint"
    assert contract.stages[0].parameters["source"] == "Paint"


def test_interaction_compiles_to_graph_without_monolithic_agent_goal():
    from winpilot.tasking.compiler import TaskContractCompiler

    contract, graph = TaskContractCompiler().compile_graph(INTERACTIVE_TASKS[0])
    assert contract.session is not None
    assert all(node.kind.value != "agent_goal" for node in graph.nodes)
    assert [
        node.config["stage"]["type"]
        for node in graph.nodes
        if node.kind.value == "task_stage"
    ] == [stage.type.value for stage in contract.stages]


def test_external_turn_is_compiled_as_verified_defer_not_forced_mutation():
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile("轮到我了，等我操作后你再根据当前界面继续")
    assert contract.intent.interaction.active is True
    assert contract.intent.interaction.turn_owner == "user"
    perform = next(stage for stage in contract.stages if stage.type.value == "PerformTurn")
    verify = next(stage for stage in contract.stages if stage.type.value == "VerifyLocalEffect")
    assert perform.parameters["turn_guard"] == "only_when_agent_owns_turn"
    assert perform.parameters["on_external_turn"] == "defer_without_mutation"
    assert verify.parameters["accept_verified_defer"] is True
