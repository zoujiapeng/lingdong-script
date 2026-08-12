from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    ("task", "expected_app"),
    [
        ("打开QQ去QQ空间里面点赞看到的所有说说", "QQ"),
        ("打开网易云音乐搜索周杰伦并播放列表的第一首歌", "网易云音乐"),
        ("去抖音搜索kimi k3", "抖音"),
        ("帮我把当前打开的onenote笔记转成world", "onenote"),
        ("用Wireshark看看最近的包里面有什么", "Wireshark"),
    ],
)
def test_composite_desktop_requests_keep_the_whole_goal(
    task: str,
    expected_app: str,
) -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        task,
        cold_start=True,
        require_gui=True,
    )

    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    stage = contract.stages[0]
    assert stage.parameters["goal"] == task
    assert stage.parameters["app"].casefold() == expected_app.casefold()
    assert "forbid_open_only" not in stage.parameters
    assert "minimum_effect_categories" not in stage.parameters
    assert "visual.spatial_interaction" in stage.execution.required_capabilities
    assert contract.requirement_coverage.complete is True
    assert contract.requirements[0].text == task
    assert contract.final_conditions == ["desktop.full_goal_verified"]


def test_explicit_drawing_application_is_not_replaced_by_paint() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile("用kirta帮我画一部手机", cold_start=True)

    assert contract.intent.source == "kirta"
    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    assert contract.stages[0].parameters["app"] == "kirta"
    assert {
        "surface_output_mode", "surface_output_subject",
        "surface_output_quality", "subject", "quality",
    }.isdisjoint(contract.stages[0].parameters)
    assert "visual.adaptive_interaction" in (
        contract.stages[0].execution.required_capabilities
    )


def test_unnamed_surface_output_uses_the_same_general_stage_from_each_entrypoint() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contracts = [
        TaskContractCompiler().compile(
            "draw a flower",
            cold_start=True,
            require_gui=require_gui,
        )
        for require_gui in (False, True)
    ]

    comparable_parameters = []
    for contract in contracts:
        assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
        stage = contract.stages[0]
        assert stage.parameters["goal"] == "draw a flower"
        assert stage.parameters["app"] == ""
        assert stage.parameters["source"] == "active_window"
        assert stage.parameters["named_applications"] == []
        assert stage.parameters["preserve_original_intent"] is True
        assert {
            "surface_output_mode", "surface_output_subject",
            "surface_output_quality", "subject", "quality",
        }.isdisjoint(stage.parameters)
        assert "visual.adaptive_interaction" in stage.execution.required_capabilities
        comparable_parameters.append({
            key: stage.parameters[key]
            for key in (
                "goal", "app", "source", "named_applications",
                "preserve_original_intent", "verification_policy",
            )
        })

    assert comparable_parameters[0] == comparable_parameters[1]


@pytest.mark.parametrize("task", ["draw", "What can you do?"])
def test_gui_entrypoint_reaches_the_live_frame_without_compile_time_clarification(
    task: str,
) -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        task,
        cold_start=True,
        require_gui=True,
    )

    assert contract.clarification_questions == []
    assert contract.session is None
    assert [stage.type.value for stage in contract.stages] == [
        "ExecuteDesktopTask",
    ]
    assert contract.stages[0].parameters["goal"] == task
    assert contract.stages[0].parameters["original_task"] == task


def test_same_board_different_user_intent_does_not_switch_runtime_mode() -> None:
    from winpilot.agent.runtime_profile import build_runtime_profile
    from winpilot.agent.tool_schemas import tool_schemas
    from winpilot.tasking.compiler import TaskContractCompiler

    tasks = (
        "On the currently visible tic-tac-toe board, place my next mark.",
        (
            "Look at the currently visible tic-tac-toe board and tell me the "
            "best move without interacting with it."
        ),
        (
            "Using the currently visible tic-tac-toe board, teach me how the "
            "game works without making a move."
        ),
    )
    contracts = [
        TaskContractCompiler().compile(
            task,
            cold_start=True,
            require_gui=True,
        )
        for task in tasks
    ]

    tool_sets = []
    native_schema_sets = []
    capability_sets = []
    for task, contract in zip(tasks, contracts):
        assert [stage.type.value for stage in contract.stages] == [
            "ExecuteDesktopTask",
        ]
        stage = contract.stages[0]
        assert stage.parameters["goal"] == task
        assert stage.parameters["original_task"] == task
        assert {
            "surface_output_mode", "surface_output_subject",
            "surface_output_quality", "subject", "quality",
        }.isdisjoint(stage.parameters)
        profile = build_runtime_profile(
            task,
            task_contract=contract,
            stage=stage,
        )
        assert profile.surface_output_mode is False
        assert profile.inline_semantic_verification is False
        tool_sets.append(set(stage.execution.model_tools))
        capability_sets.append(set(stage.execution.required_capabilities))
        native_schema_sets.append({
            item["function"]["name"]
            for item in tool_schemas(
                planning=False,
                allow_shell=False,
                allow_files=False,
                profile="lean_gui",
                native_vision=True,
                required_capabilities=profile.required_capabilities,
                stage_tools=profile.stage_tools,
                stage_type=profile.stage_type,
                surface_output_mode=profile.surface_output_mode,
            )
        })

    assert tool_sets[0] == tool_sets[1]
    assert tool_sets[1] == tool_sets[2]
    assert native_schema_sets[0] == native_schema_sets[1] == native_schema_sets[2]
    assert capability_sets[0] == capability_sets[1] == capability_sets[2]
    expected_model_tools = {
        "open_app", "launch", "list_windows", "focus_window", "observe",
        "click", "double_click", "type_text", "hotkey", "scroll", "drag", "draw_strokes",
        "mouse_down", "move_mouse", "mouse_up",
        "wait", "done", "blocked", "fail",
        "capture_artifact",
        "discover_mcp_tools", "call_mcp_tool",
    }
    assert tool_sets[0] == expected_model_tools
    assert {
        "run_action_batch", "execute_subgoal", "read_text", "screen_watch",
        "vlm_describe", "vlm_verify", "revise_plan", "complete_subgoal",
        "record_facts",
    }.isdisjoint(tool_sets[0])
    assert {
        "click", "double_click", "type_text", "scroll", "drag", "draw_strokes",
        "mouse_down", "move_mouse", "mouse_up",
    } <= native_schema_sets[0]


def test_execute_desktop_stage_passes_its_model_tools_to_runtime_profile(
    monkeypatch,
) -> None:
    import winpilot.tasking.stage_runtime as stage_runtime
    from winpilot.tasking.compiler import TaskContractCompiler

    task = "On the currently visible tic-tac-toe board, place my next mark."
    contract = TaskContractCompiler().compile(
        task,
        cold_start=True,
        require_gui=True,
    )
    stage = contract.stages[0]
    captured = {}

    class FakeRunner:
        run_id = "child-model-tool-contract"
        status = "done"
        result_text = "complete"
        usage_total = {}
        model_request_count = 1
        model_elapsed_ms = 0
        _done_verified = True
        _inline_stage_semantic_attempted = False
        _inline_stage_semantic_passed = False
        _inline_stage_semantic_last_verdict = {}
        _tool_history = [
            ("click", {"frame_x": 500, "frame_y": 500}, '{"ok": true}'),
        ]
        session = SimpleNamespace(_last_canvas_region=None)

        def run(self):
            return self.result_text

    def fake_build_runner(_goal, **kwargs):
        captured["profile"] = kwargs["runtime_profile"]
        return FakeRunner()

    class Registry:
        def record(self, predicate, **values):
            return {"predicate": predicate, **values}

    monkeypatch.setattr(stage_runtime, "build_runner", fake_build_runner)
    runtime = stage_runtime.StageRuntime()
    monkeypatch.setattr(runtime, "_evidence", lambda _context: Registry())
    monkeypatch.setattr(runtime, "_store", lambda _context: object())

    result = runtime._execute(
        stage,
        contract,
        {"run_id": "run-model-tool-contract", "runtime_state": {}},
        threading.Event(),
        time.monotonic(),
    )

    assert result.ok is True
    assert captured["profile"].model_tool_allowlist == tuple(
        stage.execution.model_tools
    )


def test_desktop_answer_can_finish_without_inventing_a_gui_mutation(monkeypatch) -> None:
    import winpilot.tasking.stage_runtime as stage_runtime
    from winpilot.tasking.compiler import TaskContractCompiler

    task = (
        "Using the currently visible tic-tac-toe board, teach me how the "
        "game works without making a move."
    )
    contract = TaskContractCompiler().compile(
        task,
        cold_start=True,
        require_gui=True,
    )
    stage = contract.stages[0]

    class FakeRunner:
        run_id = "child-visual-answer"
        status = "done"
        result_text = "The model explained the visible game without moving a piece."
        usage_total = {}
        model_request_count = 1
        model_elapsed_ms = 1
        _done_verified = True
        _inline_stage_semantic_passed = False
        _inline_stage_semantic_attempted = False
        _inline_stage_semantic_last_verdict = {}
        _tool_history = []
        session = SimpleNamespace(_last_canvas_region=None)

        def run(self):
            return self.result_text

    monkeypatch.setattr(
        stage_runtime,
        "build_runner",
        lambda *_args, **_kwargs: FakeRunner(),
    )
    result = stage_runtime.StageRuntime()._execute(
        stage,
        contract,
        {"run_id": "run-visual-answer", "runtime_state": {}},
        threading.Event(),
        time.monotonic(),
    )

    assert "forbid_open_only" not in stage.parameters
    assert "minimum_effect_categories" not in stage.parameters
    assert result.ok is True
    assert result.output["result"] == FakeRunner.result_text


def test_unseen_slider_drag_uses_generic_visual_interaction_contract() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract, graph = TaskContractCompiler().compile_graph(
        "拖动当前界面的滑块到目标位置",
        cold_start=True,
    )

    assert contract.intent.interaction.active is True
    assert contract.intent.interaction.primary_action_modality == "drag"
    assert contract.session is None
    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    stage = contract.stages[0]
    assert stage.parameters["goal"] == "拖动当前界面的滑块到目标位置"
    assert "visual.adaptive_interaction" in stage.execution.required_capabilities
    assert {"observe", "click", "drag", "scroll"} <= set(
        stage.execution.model_tools
    )
    assert all(edge.branch != "continue" for edge in graph.edges)


@pytest.mark.parametrize(
    "task",
    [
        "在当前可见窗口中，按住蓝色圆盘，沿可见通道拖进绿色目标，不能离开通道。",
        "在当前窗口中按住节点沿曲线移动到终点",
        "在当前画布里抓住手柄拖到标记处",
        "In the visible window, hold the token and drag it along the path to the target.",
    ],
)
def test_hold_and_path_phrasing_uses_generic_visual_interaction(task: str) -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(task, cold_start=True)

    assert contract.intent.interaction.active is True
    assert contract.intent.interaction.mode == "direct_visual_manipulation"
    assert contract.intent.interaction.primary_action_modality == "drag"
    assert contract.session is None
    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    assert contract.stages[0].parameters["goal"] == task
    assert "DrawArtifact" not in [stage.type.value for stage in contract.stages]


@pytest.mark.parametrize(
    "task",
    [
        (
            "在当前可见窗口中按住蓝色 HOLD 圆盘，全程不要松开；先移动到黄色 "
            "CHECKPOINT。只有到达后才会显示绿色 GOAL，请根据新画面继续移动，"
            "并在 GOAL 内释放。"
        ),
        "在当前可见窗口中，按住蓝色圆盘拖到目标",
        "在现在已打开的界面里抓住节点拖到标记处",
        "In the currently visible window hold the token and drag it to the target.",
    ],
)
def test_deictic_live_surface_is_not_parsed_as_an_application(task: str) -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(task, cold_start=True)

    assert contract.intent.source == ""
    assert contract.intent.applications == []
    assert contract.intent.interaction.active is True
    assert contract.intent.interaction.target == "active_window"
    stage = contract.stages[0]
    assert stage.type.value == "ExecuteDesktopTask"
    assert stage.parameters["source"] == "active_window"
    assert stage.parameters["goal"] == task


def test_named_application_before_hold_remains_an_application_constraint() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "在 Krita 中按住蓝色圆盘拖到目标",
        cold_start=True,
    )

    assert contract.intent.source == "Krita"
    assert contract.intent.applications == ["Krita"]
    assert contract.intent.interaction.active is True
    assert contract.intent.interaction.target == "Krita"


def test_explanation_about_holding_and_dragging_is_not_a_live_gui_command() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "解释在当前窗口中按住鼠标拖动的原理",
        cold_start=True,
    )

    assert contract.intent.interaction.active is False


@pytest.mark.parametrize("surface_noun", ["画布", "画面"])
def test_visual_surface_noun_does_not_turn_drag_into_drawing(
    surface_noun: str,
) -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        f"在当前{surface_noun}里抓住手柄拖到标记处",
        cold_start=True,
    )

    assert contract.intent.operation == ["interact"]
    assert contract.intent.interaction.action_modalities == ["drag"]
    assert contract.intent.interaction.primary_action_modality == "drag"


def test_unknown_gameplay_keeps_full_goal_and_all_spatial_primitives() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    task = "打开植物大战僵尸，观察当前局面并通过点击或拖动继续游玩"
    contract = TaskContractCompiler().compile(task, cold_start=True, max_steps=80)

    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    stage = contract.stages[0]
    assert stage.parameters["goal"] == task
    assert stage.parameters["app"] == "植物大战僵尸"
    assert stage.parameters["preserve_original_intent"] is True
    assert "visual.adaptive_interaction" in stage.execution.required_capabilities
    assert {
        "click", "drag", "draw_strokes", "observe",
    } <= set(stage.execution.model_tools)
    assert {
        "run_action_batch", "execute_subgoal", "screen_watch", "read_text",
    }.isdisjoint(stage.execution.model_tools)
    assert "DrawArtifact" not in [item.type.value for item in contract.stages]


def test_drag_to_play_relation_resolves_an_open_ended_app_name() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "拖动游玩植物大战僵尸",
        cold_start=True,
        max_steps=80,
    )

    assert contract.intent.source == "植物大战僵尸"
    assert contract.intent.applications == ["植物大战僵尸"]
    assert contract.intent.interaction.primary_action_modality == "drag"
    stage = contract.stages[0]
    assert stage.type.value == "ExecuteDesktopTask"
    assert stage.parameters["source"] == "植物大战僵尸"
    assert stage.parameters["goal"] == "拖动游玩植物大战僵尸"


def test_cold_start_carries_session_facts_without_compiler_rewrite() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.intent import TaskIntentParser

    prior = {
        "task": "用绘图岛画一朵花",
        "status": "done",
        "result": "绘图完成",
        "run_id": "prior-draw",
        "intent": {
            "operation": ["draw"],
            "source": "绘图岛",
            "destination": "",
            "channel": "",
            "recipient": "",
            "subject": "一朵花",
        },
    }
    task = "把刚刚画的画截图发给我的手机"
    parsed_without_context = TaskIntentParser().parse(task)
    contract = TaskContractCompiler().compile(
        task,
        cold_start=True,
        session_context=[prior],
        require_gui=True,
    )

    assert contract.experience_policy == "disabled"
    assert contract.intent.model_dump(mode="json") == parsed_without_context.model_dump(
        mode="json"
    )
    assert contract.session_context == [{
        "task": prior["task"],
        "status": prior["status"],
        "result": prior["result"],
        "run_id": prior["run_id"],
        "intent": prior["intent"],
        "artifacts": [],
    }]
    assert "session_reference" not in contract.intent.provenance
    assert "session_run_id" not in contract.intent.provenance
    assert contract.clarification_questions == []
    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    stage = contract.stages[0]
    assert stage.parameters["goal"] == task
    assert stage.parameters["preserve_original_intent"] is True
    assert "capture_artifact" in stage.execution.model_tools
    assert {
        "send_artifact", "open_conversation", "send_text",
    }.isdisjoint(stage.execution.model_tools)


def test_managed_artifact_handle_survives_native_context_compaction() -> None:
    from winpilot.agent.loop import AgentRunner

    artifact_id = "art-1a030986980e4c68"
    raw = json.dumps({
        "ok": True,
        "artifact_id": artifact_id,
        "artifact": {"artifact_id": artifact_id, "sha256": "a" * 64},
        "fast_path_advice": f"Use exact artifact_id {artifact_id}.",
    })

    compact = json.loads(AgentRunner._compact_tool_evidence("capture_artifact", raw))
    rolling = json.loads(AgentRunner._native_action_result_preview(raw))
    args = json.loads(AgentRunner._native_action_args_preview(
        "send_artifact", {"artifact_id": artifact_id},
    ))

    assert compact["artifact_id"] == artifact_id
    assert rolling["artifact_id"] == artifact_id
    assert args["artifact_id"] == artifact_id


def test_send_artifact_recovers_one_stale_alias_without_guessing_between_multiple(
    tmp_path,
    monkeypatch,
) -> None:
    import numpy as np

    from winpilot.agent.tools import ToolSession
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry

    monkeypatch.setattr(
        "winpilot.tasking.artifacts.capture",
        lambda _region=None: np.zeros((18, 22, 3), dtype=np.uint8),
    )
    store = ArtifactStore("run-artifact-alias", tmp_path / "artifacts")
    artifact = store.capture_artifact(scope="screen", source_window="Drawing Harbor")
    evidence = EvidenceRegistry("run-artifact-alias")
    calls: list[tuple[str, str]] = []

    class FakeAdapter:
        def paste_or_attach_artifact(self, artifact_id: str):
            calls.append(("stage", artifact_id))
            return {"ok": True, "artifact_id": artifact_id}

        def send_current_composer(self, artifact_id: str = ""):
            calls.append(("send", artifact_id))
            return {"ok": True, "artifact_id": artifact_id}

        def close(self):
            return None

    session = ToolSession(task="send the captured image")
    session.run_id = "run-artifact-alias"
    session.communication_adapter = FakeAdapter()
    session._managed_artifacts = lambda: (store, evidence)
    session._communication_adapter = lambda *_args, **_kwargs: session.communication_adapter
    try:
        payload = json.loads(session._t_send_artifact({
            "artifact_id": "artifact_1",
        }).text)
    finally:
        session.close()

    assert payload["ok"] is True
    assert payload["artifact_id"] == artifact.artifact_id
    assert payload["requested_artifact_id"] == "artifact_1"
    assert payload["artifact_resolution"]["strategy"] == (
        "sole_current_run_sendable_artifact"
    )
    assert calls == [
        ("stage", artifact.artifact_id),
        ("send", artifact.artifact_id),
    ]

    store.capture_artifact(scope="screen", source_window="Drawing Harbor")
    with pytest.raises(KeyError, match="ambiguous"):
        store.resolve_sendable("artifact_2")


def _fake_communication_provider(
    adapter_id: str,
    app_name: str,
    *,
    succeeds: bool,
    auto_discover: bool,
    closed: list[str],
):
    from winpilot.tasking.adapters import CommunicationAdapter

    class Provider(CommunicationAdapter):
        def open_conversation(self, recipient: str, *, stage_id: str = ""):
            self.recipient = recipient
            if succeeds:
                return {
                    "ok": True,
                    "recipient": recipient,
                    "target_hwnd": 777,
                }
            return {
                "ok": False,
                "failure_type": "recipient_not_resolved",
                "error": "recipient was not visible",
            }

        def resolve_composer(self):
            return {"ok": succeeds, "target_hwnd": 777}

        def paste_or_attach_artifact(self, artifact_id: str, *, stage_id: str = ""):
            return {"ok": succeeds, "artifact_id": artifact_id}

        def send_current_composer(self, artifact_id: str = "", *, stage_id: str = ""):
            return {"ok": succeeds, "artifact_id": artifact_id}

        def send_text(self, text: str, *, stage_id: str = ""):
            return {"ok": succeeds, "text": text}

        def verify_latest_outbound(
            self,
            *,
            artifact_id: str = "",
            text: str = "",
            stage_id: str = "",
        ):
            return {"ok": succeeds, "artifact_id": artifact_id, "text": text}

        def close(self):
            closed.append(adapter_id)

    Provider.id = adapter_id
    Provider.app_name = app_name
    Provider.channels = (adapter_id,)
    Provider.auto_discover = auto_discover
    return Provider


def test_unresolved_channel_discovers_an_opted_in_plugin_provider(monkeypatch) -> None:
    from winpilot.agent.tools import ToolSession
    from winpilot.perception import apps, win32
    from winpilot.tasking.adapters import DomainAdapterRegistry

    monkeypatch.setattr(DomainAdapterRegistry, "_types", {})
    monkeypatch.setattr(DomainAdapterRegistry, "_channel_names", {})
    closed: list[str] = []
    alpha = _fake_communication_provider(
        "alpha", "Alpha Messenger",
        succeeds=False, auto_discover=True, closed=closed,
    )
    beta = _fake_communication_provider(
        "beta", "Beta Messenger",
        succeeds=True, auto_discover=True, closed=closed,
    )
    DomainAdapterRegistry.register(alpha)
    DomainAdapterRegistry.register(beta)

    monkeypatch.setattr(
        win32,
        "find_app",
        lambda name: {
            "running": name == "Beta Messenger",
            "has_visible_window": name == "Beta Messenger",
        },
    )
    monkeypatch.setattr(win32, "find_window", lambda _name: None)
    monkeypatch.setattr(
        apps,
        "resolve",
        lambda name, max_results=3: [{
            "name": name,
            "match_score": 100,
            "match_kind": "literal_or_alias",
        }],
    )

    session = ToolSession(task="send through a configured communication plugin")
    session.run_id = "run-route-discovery"
    session._managed_artifacts = lambda: (object(), object())
    try:
        payload = json.loads(session._t_open_conversation({
            "recipient": "Test Contact",
        }).text)
        assert payload["ok"] is True
        assert payload["route_discovered"] is True
        assert payload["channel"] == "beta"
        assert payload["recipient"] == "Test Contact"
        assert payload["conversation"]["ok"] is True
        assert payload["composer"]["ok"] is True
        assert isinstance(session.communication_adapter, beta)
        assert closed == []
    finally:
        session.close()

    assert closed == ["beta"]


def test_registered_adapter_discovery_deduplicates_channel_aliases(monkeypatch) -> None:
    from winpilot.tasking.adapters import DomainAdapterRegistry

    monkeypatch.setattr(DomainAdapterRegistry, "_types", {})
    monkeypatch.setattr(DomainAdapterRegistry, "_channel_names", {})
    assert DomainAdapterRegistry.registered_adapters() == []

    provider = _fake_communication_provider(
        "fictional", "Fictional Messenger",
        succeeds=True, auto_discover=False, closed=[],
    )
    DomainAdapterRegistry.register(
        provider,
        channels=("fictional", "Fictional Messenger", "FM"),
    )
    descriptors = DomainAdapterRegistry.registered_adapters()

    assert len(descriptors) == 1
    assert descriptors[0]["adapter_id"] == "fictional"
    assert set(descriptors[0]["channels"]) == {
        "fictional", "Fictional Messenger", "FM",
    }
    assert descriptors[0]["auto_discover"] is False


def test_meta_question_uses_text_response_route_not_gui() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile("那你干嘛", cold_start=True)

    assert contract.intent.operation == ["respond"]
    assert [stage.type.value for stage in contract.stages] == ["ExecuteSemanticTask"]
    assert contract.stages[0].parameters["response_mode"] == "conversation"
    assert contract.stages[0].execution is not None
    assert "click" not in contract.stages[0].execution.model_tools


@pytest.mark.parametrize(
    "task",
    [
        "把这个弄好",
        "根据当前情况完成剩余部分",
        "Resolve the visible mismatch and stop when the screen confirms success",
        "处理眼前的内容直到出现成功提示",
    ],
)
def test_untyped_visible_goal_still_receives_the_complete_gui_loop(task: str) -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        task,
        cold_start=True,
        require_gui=True,
    )

    # This is deliberately an untyped fallback test: success must not depend
    # on adding another task verb or application keyword to the parser.
    assert contract.intent.operation == []
    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    stage = contract.stages[0]
    assert stage.parameters["goal"] == task
    assert stage.parameters["preserve_original_intent"] is True
    assert "forbid_open_only" not in stage.parameters
    assert "minimum_effect_categories" not in stage.parameters
    assert {
        "open_app", "observe", "click", "scroll", "drag", "type_text",
    } <= set(stage.execution.model_tools)
    assert contract.requirements[0].text == task
    assert contract.final_conditions == ["desktop.full_goal_verified"]


def test_capability_demonstration_uses_the_same_full_goal_contract() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "演示一下你有什么能力",
        cold_start=True,
        require_gui=True,
    )
    stage = contract.stages[0]

    assert stage.type.value == "ExecuteDesktopTask"
    assert stage.parameters["verification_policy"] == "full_goal_coverage"
    assert "minimum_effect_categories" not in stage.parameters
    assert "lifecycle" not in stage.parameters
    assert contract.requirements[0].kind == "desktop_workflow"


def test_continuous_chat_wording_does_not_install_desktop_lifecycle() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    task = (
        "去qq找我的手机持续聊天,他如果发@电脑就说明在命令你,"
        "你要照做"
    )
    contract = TaskContractCompiler().compile(
        task,
        cold_start=True,
        require_gui=True,
    )

    assert contract.clarification_questions == []
    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    stage = contract.stages[0]
    assert stage.parameters["goal"] == task
    assert stage.parameters["preserve_original_intent"] is True
    assert "lifecycle" not in stage.parameters
    assert stage.parameters["verification_policy"] == "full_goal_coverage"
    assert contract.requirements[0].text == task
    assert {
        "communication_session", "command_trigger", "command_safety_policy",
    }.isdisjoint(stage.parameters)
    assert {
        "open_conversation", "send_text", "send_artifact",
    }.isdisjoint(stage.execution.model_tools)
    assert {"observe", "click", "type_text"} <= set(
        stage.execution.model_tools
    )
    assert {
        "record_facts", "revise_plan", "read_text",
    }.isdisjoint(stage.execution.model_tools)


def test_installed_app_resolution_uses_binary_aliases_and_typo_fuzzing(monkeypatch) -> None:
    from winpilot.perception import apps

    monkeypatch.setattr(apps, "_index", lambda: [
        {
            "name": "Nebula Player",
            "target": r"C:\Start\Nebula Player.lnk",
            "kind": "lnk",
            "aliases": [r"C:\Programs\Nebula\nebula-player.exe"],
        },
        {
            "name": "Aurora Render",
            "target": r"C:\Start\Aurora Render.lnk",
            "kind": "lnk",
            "aliases": [r"C:\Programs\Aurora\aurora-render.exe"],
        },
    ])

    assert apps.resolve("nebula-player")[0]["name"] == "Nebula Player"
    fuzzy = apps.resolve("Aurroa Render")[0]
    assert fuzzy["name"] == "Aurora Render"
    assert fuzzy["match_kind"] == "fuzzy"


def test_launch_application_prefers_exact_path_command_over_fuzzy_index(
    monkeypatch,
) -> None:
    from winpilot.perception import apps

    launched: list[list[str]] = []

    def fake_popen(argv, **_kwargs):
        launched.append(list(argv))
        return None

    monkeypatch.setattr(apps.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(apps, "_index", lambda: [
        {
            "name": "Notepad++",
            "target": r"C:\Program Files\Notepad++\notepad++.exe",
            "kind": "exe",
            "aliases": ["notepad++", "notepad"],
        },
    ])
    monkeypatch.setattr(apps, "resolve", lambda _query, max_results=1: [
        {
            "name": "Notepad++",
            "target": r"C:\Program Files\Notepad++\notepad++.exe",
            "kind": "exe",
            "aliases": ["notepad++", "notepad"],
        },
    ])
    # An exact PATH command must win over the fuzzy "notepad++" index hit.
    monkeypatch.setattr(
        apps.shutil,
        "which",
        lambda name: (
            r"C:\WINDOWS\system32\notepad.EXE"
            if name == "notepad"
            else None
        ),
    )
    # No visible notepad instance exists in this test environment.
    monkeypatch.setattr(apps, "_activate_existing_instance", lambda _path: None)

    result = apps.launch_application("notepad")

    assert result["ok"] is True
    assert result["via"] == "path"
    assert result["target"].endswith("notepad.EXE")
    assert launched == []


def test_launch_application_uses_index_for_non_path_display_names(monkeypatch) -> None:
    from winpilot.perception import apps

    monkeypatch.setattr(apps, "_index", lambda: [
        {
            "name": "Nebula Player",
            "target": r"C:\Start\Nebula Player.lnk",
            "kind": "lnk",
            "aliases": [],
        },
    ])
    monkeypatch.setattr(apps.shutil, "which", lambda _name: None)
    started: list[str] = []
    monkeypatch.setattr(apps.os, "startfile", lambda target: started.append(target))

    result = apps.launch_application("Nebula Player")

    assert result["ok"] is True
    assert result["via"] == "lnk"
    assert result["target"] == r"C:\Start\Nebula Player.lnk"
    assert started == [r"C:\Start\Nebula Player.lnk"]


def test_launch_application_rejects_empty_name() -> None:
    from winpilot.perception import apps

    result = apps.launch_application("   ")
    assert result["ok"] is False
    assert "empty" in result["error"]


def test_activate_resolved_entry_uses_process_name_for_appid_entries(
    monkeypatch,
) -> None:
    """UWP app entries resolve to AppIDs, not exe paths.

    A running app whose window title is a document/song name ("江南 - 林俊杰")
    must still be activated via its process file name; the display-name title
    fallback would miss it entirely.
    """
    from winpilot.perception import apps

    entry = {
        "kind": "appid",
        "name": "网易云音乐",
        "target": "1F8B0F94.122165AE053F_j2p0p5q0044a6!CLOUDMUSIC",
        "process_name": "cloudmusic.exe",
    }
    monkeypatch.setattr(apps, "_activate_existing_instance", lambda _path: None)
    monkeypatch.setattr(
        apps,
        "_activate_existing_by_process_name",
        lambda name: (
            {
                "candidate_count": 1,
                "activated_hwnd": 2426606,
                "candidates": [],
            }
            if name == "cloudmusic.exe"
            else None
        ),
    )

    assert apps._activate_resolved_entry(entry)["activated_hwnd"] == 2426606


def test_activate_resolved_entry_does_not_reuse_by_localized_window_title(
    monkeypatch,
) -> None:
    """Display text is not stable application identity."""
    from winpilot.perception import apps

    assert apps._activate_resolved_entry({
        "kind": "appid",
        "name": "网易云音乐",
        "target": "package!app",
    }) is None
    assert not hasattr(apps, "_activate_existing_by_title")


def test_activate_existing_by_process_name_finds_window_by_process(
    monkeypatch,
) -> None:
    """Process-name activation finds a song-titled app window."""
    from winpilot.perception import apps

    windows = [
        (3001, "江南 - 林俊杰", "cloudmusic.exe"),
        (3002, "网易云音乐 - Google Chrome", "chrome.exe"),
    ]

    def fake_enum_windows(callback, _unused):
        for hwnd, _title, _process in windows:
            callback(hwnd, None)
        return True

    fake_win32gui = SimpleNamespace(
        EnumWindows=fake_enum_windows,
        IsWindowVisible=lambda h: True,
        IsIconic=lambda h: False,
        GetWindowText=lambda h: next(
            title for hwnd2, title, _p in windows if hwnd2 == h
        ),
        GetForegroundWindow=lambda: 3001,
        GetAncestor=lambda h, _flag: h,
        SetForegroundWindow=lambda h: True,
        BringWindowToTop=lambda h: True,
    )
    import sys as _sys

    monkeypatch.setitem(_sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setattr(
        apps,
        "_window_process_name",
        lambda h: next(
            process for hwnd2, _t, process in windows if hwnd2 == h
        ),
    )

    existing = apps._activate_existing_by_process_name("cloudmusic.exe")

    assert existing["activated_hwnd"] == 3001
    assert existing["candidate_count"] == 1


def test_activate_existing_restores_tray_hidden_window_fallback(
    monkeypatch,
) -> None:
    """A tray-hidden/minimized window is still the same running instance.

    When an app's only window is hidden in the notification area (QQ's
    classic "close to tray" behavior), launch must restore and activate it
    instead of silently starting a second, possibly unauthenticated,
    instance.
    """
    from winpilot.perception import apps

    windows = [
        (4001, "QQ", "QQ.exe"),
    ]
    state = {"activated": None, "shown": None}

    def fake_enum_windows(callback, _unused):
        for hwnd, _title, _process in windows:
            callback(hwnd, None)
        return True

    fake_win32gui = SimpleNamespace(
        EnumWindows=fake_enum_windows,
        # The QQ window is hidden in the tray: not visible, not iconic.
        IsWindowVisible=lambda h: False,
        IsIconic=lambda h: False,
        GetWindowText=lambda h: next(
            title for hwnd2, title, _p in windows if hwnd2 == h
        ),
        GetForegroundWindow=lambda: state["activated"] or 9000,
        GetAncestor=lambda h, _flag: h,
        ShowWindow=lambda h, cmd: state.update(shown=cmd) or True,
        SetForegroundWindow=lambda h: state.update(activated=h) or True,
        BringWindowToTop=lambda h: True,
    )
    import sys as _sys

    monkeypatch.setitem(_sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setattr(
        apps,
        "_window_process_name",
        lambda h: next(
            process for hwnd2, _t, process in windows if hwnd2 == h
        ).casefold(),
    )

    existing = apps._activate_existing_by_process_name("QQ.exe")

    assert existing["activated_hwnd"] == 4001
    assert state["shown"] is not None  # SW_RESTORE was called
    assert state["activated"] == 4001


def test_launch_application_does_not_choose_or_start_when_process_has_multiple_windows(
    monkeypatch,
) -> None:
    from winpilot.perception import apps

    started: list[str] = []
    monkeypatch.setattr(apps.shutil, "which", lambda _name: None)
    monkeypatch.setattr(apps.os, "startfile", lambda target: started.append(target))
    monkeypatch.setattr(apps, "_activate_resolved_entry", lambda _entry: {
        "candidate_count": 2,
        "candidates": [
            {"hwnd": 7101, "title": "chat A", "visible": True, "minimized": False},
            {"hwnd": 7102, "title": "chat B", "visible": True, "minimized": False},
        ],
        "note": (
            "2 existing window candidates; no new instance started; choose on "
            "the current screen"
        ),
    })

    result = apps.launch_application(
        "Chat App",
        resolver=lambda _name: [{
            "kind": "exe",
            "name": "Chat App",
            "target": r"C:\Apps\chat.exe",
        }],
    )

    assert result["ok"] is True
    assert result["via"] == "existing"
    assert result["candidate_count"] == 2
    assert "choose on the current screen" in result["note"]
    assert "activated_hwnd" not in result
    assert started == []


def test_existing_activation_failure_is_reported_without_starting_duplicate(
    monkeypatch,
) -> None:
    from winpilot.perception import apps

    monkeypatch.setattr(apps.shutil, "which", lambda _name: r"C:\Apps\chat.exe")
    monkeypatch.setattr(apps, "_activate_existing_instance", lambda _path: {
        "candidate_count": 1,
        "candidates": [
            {"hwnd": 7201, "title": "chat", "visible": True, "minimized": False},
        ],
        "note": (
            "1 existing window found but it did not become foreground; no new "
            "instance started; re-observe the current screen"
        ),
    })
    started: list[str] = []
    monkeypatch.setattr(apps.os, "startfile", lambda target: started.append(target))

    result = apps.launch_application("chat")

    assert result["via"] == "existing"
    assert "did not become foreground" in result["note"]
    assert "activated_hwnd" not in result
    assert started == []


def test_open_application_stage_rejects_controller_ok_without_verification(monkeypatch) -> None:
    from winpilot.agent.tool_result import ToolResult
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.stage_runtime import StageRuntime

    contract = TaskContractCompiler().compile("打开计算器", cold_start=True)
    stage = contract.stages[0]
    runtime = StageRuntime()

    class FakeSession:
        def __init__(self, **_kwargs):
            self.run_id = ""

        def dispatch(self, _name, _args):
            return ToolResult(json.dumps({
                "ok": True,
                "verified": False,
                "target_hwnd": 123,
                "title": "Command Prompt",
            }))

        def close(self):
            return None

    monkeypatch.setattr("winpilot.tasking.stage_runtime.ToolSession", FakeSession)
    context = {"run_id": "run-open-false", "runtime_state": {}}

    with pytest.raises(RuntimeError) as caught:
        runtime._execute(stage, contract, context, threading.Event(), time.monotonic())

    assert caught.value.failure_type == "application_target_unverified"


def test_complete_desktop_stage_enters_agent_and_ignores_legacy_effect_gate(monkeypatch) -> None:
    import winpilot.tasking.stage_runtime as stage_runtime
    from winpilot.tasking.compiler import TaskContractCompiler

    task = (
        "\u6253\u5f00\u7f51\u6613\u4e91\u97f3\u4e50\u641c\u7d22\u6881\u9759\u8339\u5e76\u64ad\u653e"
        "\u5217\u8868\u7684\u7b2c\u4e00\u9996\u6b4c,\u7136\u540e\u8c03\u5230\u6700\u5927\u58f0"
    )
    contract = TaskContractCompiler().compile(
        task,
        cold_start=True,
        require_gui=True,
    )
    stage = contract.stages[0]
    # A stale/evaluation-only parameter must not overrule the unified visual
    # Agent's verified completion verdict.
    stage.parameters["minimum_effect_categories"] = 99
    built = []

    class ForbiddenPreOpenSession:
        def __init__(self, **_kwargs):
            raise AssertionError("StageRuntime must not pre-open a semantic desktop app")

    class FakeRunner:
        run_id = "child-semantic-desktop"
        status = "done"
        result_text = "done"
        usage_total = {}
        model_request_count = 1
        model_elapsed_ms = 1
        _done_verified = True
        _inline_stage_semantic_passed = False
        _inline_stage_semantic_attempted = False
        _inline_stage_semantic_last_verdict = {}
        _tool_history = [
            ("type_text", {"text": "search query"}, json.dumps({"ok": True})),
        ]
        session = SimpleNamespace(_last_canvas_region=None)

        def bind_target_window(self, *_args):
            raise AssertionError("an unrelated handoff window must not be bound")

        def run(self):
            return {"status": "done"}

    def fake_build_runner(goal, **_kwargs):
        built.append(goal)
        return FakeRunner()

    monkeypatch.setattr(stage_runtime, "ToolSession", ForbiddenPreOpenSession)
    monkeypatch.setattr(stage_runtime, "build_runner", fake_build_runner)
    context = {
        "run_id": "run-semantic-desktop-owner",
        "runtime_state": {},
    }

    result = stage_runtime.StageRuntime()._execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
    )

    assert result.ok is True
    assert built == [task]
    assert result.output["model_calls"] == 1


def test_desktop_effect_summary_reports_open_only_and_category_count() -> None:
    from winpilot.tasking.stage_runtime import StageRuntime

    open_only = StageRuntime._desktop_effect_summary([
        ("open_app", {"name": "Notepad"}, json.dumps({"ok": True, "verified": True})),
    ])
    typed_description = StageRuntime._desktop_effect_summary([
        ("type_text", {"text": "I can open apps and draw"}, json.dumps({"ok": True})),
    ])

    assert open_only["open_only"] is True
    assert typed_description["effect_category_count"] == 1


def test_window_title_contains_literal_uses_the_canonical_title_check(monkeypatch) -> None:
    from winpilot.action import verify

    monkeypatch.setattr(
        verify,
        "_check_window_title",
        lambda needle, hwnd: (needle == "Nebula Monitor" and hwnd == 123, "Nebula Monitor"),
    )
    outcome = verify.check(
        {"window_title_contains": "Nebula Monitor"}, hwnd=123, timeout_s=10,
    )

    assert outcome.verified is True
    assert outcome.method == "win32"


def test_wait_with_truly_unknown_condition_fails_without_polling() -> None:
    from winpilot.action import verify

    started = time.monotonic()
    outcome = verify.check({"imaginary_condition": "Wireshark"}, timeout_s=10)

    assert outcome.verified is False
    assert outcome.method == "schema"
    assert outcome.trace["poll_count"] == 0
    assert time.monotonic() - started < 0.5


def test_explicit_url_is_not_blocked_by_task_string_classification(monkeypatch) -> None:
    from winpilot.agent import tools as tools_mod
    from winpilot.agent.tools import ToolSession

    session = ToolSession(
        task="open the requested target in Nebula Desktop",
        task_intent={"domain": "desktop", "source": "Nebula Desktop"},
    )
    launched = []
    monkeypatch.setattr(session, "_resolve_launch_target", lambda _command: None)
    monkeypatch.setattr(session, "_visible_window_snapshot", lambda _win32: {})
    monkeypatch.setattr(
        tools_mod.executor,
        "launch",
        lambda command: launched.append(command) or {"ok": True},
    )
    monkeypatch.setattr(
        tools_mod.verify,
        "run_with_verify",
        lambda _name, action, **_kwargs: {**action(), "verified": True},
    )
    try:
        result = json.loads(session._t_launch({
            "command": "https://example.test/visible-target",
        }).text)
    finally:
        session.close()

    assert result["ok"] is True
    assert launched == ["https://example.test/visible-target"]


def test_desktop_stage_exposes_optional_mcp_but_keeps_gui_controller() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "帮我把当前打开的onenote笔记转成world",
        cold_start=True,
    )
    tools = set(contract.stages[0].execution.model_tools)

    assert {"discover_mcp_tools", "call_mcp_tool"} <= tools
    assert {"open_app", "observe", "click", "type_text"} <= tools
    assert {"execute_subgoal", "run_action_batch", "read_text"}.isdisjoint(tools)


def test_calculator_natural_language_is_an_app_scoped_desktop_task() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    task = (
        "\u7528\u8ba1\u7b97\u5668\u8ba1\u7b9786\u548c79\u7684\u548c\uff0c"
        "\u786e\u8ba4\u7ed3\u679c\u662f165\u3002\u5fc5\u987b\u901a\u8fc7GUI\u64cd\u4f5c\u3002"
    )
    contract = TaskContractCompiler().compile(task, cold_start=True)

    assert contract.intent.source == "\u8ba1\u7b97\u5668"
    assert "operate" in contract.intent.operation
    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    assert contract.stages[0].parameters["app"] == "\u8ba1\u7b97\u5668"


def test_type_text_schema_does_not_expose_keypad_fallback_tactics() -> None:
    from winpilot.agent.tool_schemas import tool_schemas

    type_tool = next(
        tool["function"]
        for tool in tool_schemas()
        if tool["function"]["name"] == "type_text"
    )

    assert "keyboard_mode" not in type_tool["parameters"]["properties"]
    assert "keypad" not in type_tool["description"].casefold()
    assert "fallback" not in type_tool["description"].casefold()


def test_unknown_mcp_server_does_not_fall_back_to_querying_every_server(monkeypatch) -> None:
    from winpilot.integrations import mcp_client

    monkeypatch.setattr(mcp_client, "configured_servers", lambda: {"known": {}})
    result = mcp_client.discover(server_name="missing")

    assert result["queried_server_count"] == 0
    assert result["tools"] == []
