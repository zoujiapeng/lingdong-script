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
    assert stage.parameters["forbid_open_only"] is True
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
    assert contract.stages[0].parameters["surface_output_mode"] == "continuous_pointer"
    assert {
        "visual.adaptive_interaction",
        "visual.surface_manipulation",
    } <= set(
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
        assert stage.parameters["surface_output_mode"] == "continuous_pointer"
        assert {
            "visual.adaptive_interaction",
            "visual.surface_manipulation",
        } <= set(stage.execution.required_capabilities)
        comparable_parameters.append({
            key: stage.parameters[key]
            for key in (
                "goal", "app", "source", "named_applications",
                "preserve_original_intent", "surface_output_mode",
                "verification_policy",
            )
        })

    assert comparable_parameters[0] == comparable_parameters[1]


def test_unseen_slider_drag_uses_generic_visual_interaction_contract() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract, graph = TaskContractCompiler().compile_graph(
        "拖动当前界面的滑块到目标位置",
        cold_start=True,
    )

    assert contract.intent.interaction.active is True
    assert contract.intent.interaction.primary_action_modality == "drag"
    assert [stage.type.value for stage in contract.stages] == [
        "ObserveScene", "InferInteraction", "PerformTurn", "VerifyLocalEffect",
    ]
    perform = next(stage for stage in contract.stages if stage.type.value == "PerformTurn")
    assert "visual.adaptive_interaction" in perform.execution.required_capabilities
    assert {"execute_subgoal", "run_action_batch"} <= set(perform.execution.tools)
    verify = next(stage for stage in contract.stages if stage.type.value == "VerifyLocalEffect")
    infer = next(stage for stage in contract.stages if stage.type.value == "InferInteraction")
    assert any(
        edge.source == verify.id
        and edge.target == infer.id
        and edge.branch == "continue"
        for edge in graph.edges
    )


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
    assert [stage.type.value for stage in contract.stages] == [
        "ObserveScene", "InferInteraction", "PerformTurn", "VerifyLocalEffect",
    ]
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
    observe = contract.stages[0]
    assert observe.type.value == "ObserveScene"
    assert observe.parameters["source"] == "active_window"


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
        "click", "drag", "draw_strokes", "run_action_batch",
        "execute_subgoal", "observe", "screen_watch",
    } <= set(stage.execution.model_tools)
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
    observe = contract.stages[0]
    assert observe.type.value == "ObserveScene"
    assert observe.parameters["source"] == "植物大战僵尸"


def test_cold_start_keeps_short_term_antecedent_but_not_experience() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler
    from winpilot.tasking.stage_runtime import StageRuntime

    prior = {
        "task": "用画板画一朵花",
        "status": "done",
        "result": "绘图完成",
        "run_id": "prior-draw",
        "intent": {
            "operation": ["draw"],
            "source": "Paint",
            "destination": "",
            "channel": "",
            "recipient": "",
            "subject": "一朵花",
        },
    }
    contract = TaskContractCompiler().compile(
        "把刚刚画的画截图发给我的手机",
        cold_start=True,
        session_context=[prior],
    )

    assert contract.experience_policy == "disabled"
    assert contract.intent.source == "Paint"
    assert contract.intent.operation == ["capture", "send"]
    assert "subject" not in contract.intent.ambiguous_fields
    assert contract.intent.provenance["session_run_id"] == "prior-draw"
    assert contract.clarification_questions == []
    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    stage = contract.stages[0]
    assert stage.parameters["preserve_original_intent"] is True
    assert stage.parameters["runtime_resolution_fields"] == ["channel"]
    assert stage.parameters["source"] == "Paint"
    assert {
        "capture_artifact", "find_program", "list_installed_apps",
        "record_facts", "revise_plan", "send_artifact",
    } <= set(stage.execution.model_tools)
    scoped_intent = StageRuntime._stage_scoped_intent(
        contract,
        stage,
        stage.parameters["goal"],
    )
    assert scoped_intent.operation == ["capture", "send"]
    assert scoped_intent.ambiguous_fields == ["channel"]


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
    artifact = store.capture_artifact(scope="screen", source_window="Paint")
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

    store.capture_artifact(scope="screen", source_window="Paint")
    with pytest.raises(KeyError, match="ambiguous"):
        store.resolve_sendable("artifact_2")


def test_unresolved_channel_discovers_a_grounded_verified_adapter(monkeypatch) -> None:
    from winpilot.agent.tools import ToolSession
    from winpilot.perception import apps, win32
    from winpilot.tasking.adapters import DomainAdapterRegistry

    closed: list[str] = []

    class FakeAdapter:
        def __init__(self, adapter_id: str, *, succeeds: bool):
            self.id = adapter_id
            self.succeeds = succeeds
            self.session = SimpleNamespace(hwnd=0, target_hint=adapter_id)

        def open_conversation(self, recipient: str):
            if self.succeeds:
                return {"ok": True, "recipient": recipient, "strategy": "verified-header"}
            return {
                "ok": False,
                "failure_type": "recipient_not_resolved",
                "error": "recipient was not visible",
            }

        def resolve_composer(self):
            return {"ok": self.succeeds, "method": "verified-composer"}

        def close(self):
            closed.append(self.id)

    descriptors = [
        {
            "adapter_id": "qq",
            "app_name": "QQ",
            "component": "qq_adapter",
            "channels": ["qq", "QQ"],
        },
        {
            "adapter_id": "wechat",
            "app_name": "微信",
            "component": "wechat_adapter",
            "channels": ["wechat", "微信"],
        },
    ]
    adapters = {
        "qq": FakeAdapter("qq", succeeds=False),
        "wechat": FakeAdapter("wechat", succeeds=True),
    }
    monkeypatch.setattr(
        DomainAdapterRegistry,
        "registered_adapters",
        classmethod(lambda cls: descriptors),
    )
    monkeypatch.setattr(
        DomainAdapterRegistry,
        "create",
        classmethod(lambda cls, channel, **_kwargs: adapters[channel]),
    )
    monkeypatch.setattr(
        win32,
        "find_app",
        lambda name: {
            "running": name == "微信",
            "has_visible_window": name == "微信",
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

    session = ToolSession(task="send the current artifact to a known recipient")
    session.run_id = "run-route-discovery"
    try:
        payload = json.loads(session._t_open_conversation({
            "recipient": "我的手机",
        }).text)
        assert payload["ok"] is True
        assert payload["route_discovered"] is True
        assert payload["channel"] == "wechat"
        assert payload["recipient"] == "我的手机"
        assert payload["conversation"]["ok"] is True
        assert payload["composer"]["ok"] is True
        assert session.communication_adapter is adapters["wechat"]
        assert closed == []
    finally:
        session.close()

    assert closed == ["wechat"]


def test_registered_adapter_discovery_deduplicates_channel_aliases() -> None:
    from winpilot.tasking.adapters import DomainAdapterRegistry

    descriptors = DomainAdapterRegistry.registered_adapters()

    assert {item["adapter_id"] for item in descriptors} >= {"qq", "wechat"}
    wechat = next(item for item in descriptors if item["adapter_id"] == "wechat")
    assert {"wechat", "微信"} <= set(wechat["channels"])


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
    assert stage.parameters["forbid_open_only"] is True
    assert {
        "open_app", "observe", "click", "scroll", "drag", "type_text",
    } <= set(stage.execution.model_tools)
    assert contract.requirements[0].text == task
    assert contract.final_conditions == ["desktop.full_goal_verified"]


def test_capability_demonstration_requires_real_diverse_effects() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile("演示一下你有什么能力", cold_start=True)
    stage = contract.stages[0]

    assert stage.type.value == "ExecuteDesktopTask"
    assert stage.parameters["verification_policy"] == "capability_demonstration"
    assert stage.parameters["minimum_effect_categories"] == 2


def test_persistent_desktop_goal_uses_the_generic_adaptive_runtime() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "去qq找我的手机持续聊天,他如果发@电脑就说明在命令你,你要照做",
        cold_start=True,
    )

    assert contract.intent.send_mode == "continuous_chat"
    assert contract.intent.channel == "QQ"
    assert contract.intent.recipient == "我的手机"
    assert contract.clarification_questions == []
    assert [stage.type.value for stage in contract.stages] == ["ExecuteDesktopTask"]
    stage = contract.stages[0]
    assert stage.parameters["preserve_original_intent"] is True
    assert stage.parameters["lifecycle"] == "persistent"
    assert stage.parameters["verification_policy"] == "full_goal_coverage"
    assert {
        "communication_session", "command_trigger", "command_safety_policy",
    }.isdisjoint(stage.parameters)
    assert {
        "find_program", "list_installed_apps", "record_facts", "revise_plan",
        "open_conversation", "read_text", "send_text",
    } <= set(
        stage.execution.model_tools
    )


def test_installed_app_resolution_uses_binary_aliases_and_typo_fuzzing(monkeypatch) -> None:
    from winpilot.perception import apps

    monkeypatch.setattr(apps, "_system_tool_entries", lambda: [])
    monkeypatch.setattr(apps, "_wps_suite_entries", lambda: [])
    monkeypatch.setattr(apps, "_index", lambda: [
        {
            "name": "抖音",
            "target": r"C:\Start\抖音.lnk",
            "kind": "lnk",
            "aliases": [r"C:\Program Files\ByteDance\douyin\douyin.exe"],
        },
        {
            "name": "Krita (x64)",
            "target": r"C:\Start\Krita.lnk",
            "kind": "lnk",
            "aliases": [r"C:\Program Files\Krita\bin\krita.exe"],
        },
    ])

    assert apps.resolve("douyin")[0]["name"] == "抖音"
    fuzzy = apps.resolve("kirta")[0]
    assert fuzzy["name"] == "Krita (x64)"
    assert fuzzy["match_kind"] == "fuzzy"


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


def test_desktop_effect_gate_rejects_open_only_and_typed_capability_list() -> None:
    from winpilot.tasking.stage_runtime import StageRuntime

    open_only = StageRuntime._desktop_effect_summary([
        ("open_app", {"name": "Notepad"}, json.dumps({"ok": True, "verified": True})),
    ])
    typed_description = StageRuntime._desktop_effect_summary([
        ("type_text", {"text": "I can open apps and draw"}, json.dumps({"ok": True})),
    ])

    assert open_only["open_only"] is True
    assert typed_description["effect_category_count"] == 1


def test_window_title_contains_alias_uses_the_canonical_title_check(monkeypatch) -> None:
    from winpilot.action import verify

    monkeypatch.setattr(
        verify,
        "_check_window_title",
        lambda needle, hwnd: (needle == "Wireshark" and hwnd == 123, "Wireshark"),
    )
    outcome = verify.check(
        {"window_title_contains": "Wireshark"}, hwnd=123, timeout_s=10,
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


def test_explicit_desktop_app_blocks_unrequested_web_fallback() -> None:
    from winpilot.agent.tools import ToolSession

    session = ToolSession(
        task="去抖音搜索kimi k3",
        task_intent={"domain": "desktop", "source": "抖音"},
    )
    try:
        result = json.loads(session._t_launch({
            "command": "https://www.douyin.com/search/kimi%20k3",
        }).text)
    finally:
        session.close()

    assert result["ok"] is False
    assert result["failure_type"] == "application_constraint_violation"


def test_desktop_stage_exposes_optional_mcp_but_keeps_gui_controller() -> None:
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(
        "帮我把当前打开的onenote笔记转成world",
        cold_start=True,
    )
    tools = set(contract.stages[0].execution.model_tools)

    assert {"discover_mcp_tools", "call_mcp_tool"} <= tools
    assert {"open_app", "observe", "click", "type_text", "execute_subgoal"} <= tools


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


def test_arithmetic_type_without_visual_effect_falls_back_to_semantic_keypad(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    import winpilot.agent.interaction_tools as interaction_tools
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.fast.subgoal import SubgoalExecutor
    from winpilot.utils.screenshot import Region

    captured: dict[str, object] = {}

    fake_executor = SimpleNamespace(
        cooperative_input_guard=lambda *_args, **_kwargs: None,
        type_text=lambda text, **_kwargs: {"ok": True, "typed": len(text), "via": "vk"},
        focus_window=lambda _hwnd: {"ok": True},
    )

    def run_with_verify(_name, action, **_kwargs):
        return {**action(), "attempts": 1, "post_state": {"foreground_hwnd": 10}}

    def fake_execute(_self, args):
        captured.update(args)
        return ToolResult(json.dumps({"ok": True, "verified": True}))

    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(
        tools_mod,
        "verify",
        SimpleNamespace(run_with_verify=run_with_verify),
    )
    monkeypatch.setattr(
        tools_mod,
        "win32gui",
        SimpleNamespace(GetForegroundWindow=lambda: 10),
    )
    monkeypatch.setattr(tools_mod, "capture", lambda _region: object())
    monkeypatch.setattr(interaction_tools, "window_region", lambda _hwnd: Region(0, 0, 400, 600))
    monkeypatch.setattr(interaction_tools.vision_cv, "diff_ratio", lambda _a, _b: 0.0)
    monkeypatch.setattr(SubgoalExecutor, "execute", fake_execute)

    session = ToolSession(task="calculate 86+79 in the calculator")
    session.hwnd = 10
    session._verification_watcher = lambda: None
    session._fast_controller = lambda: object()
    try:
        payload = json.loads(session._t_type_text({"text": "86+79="}).text)
    finally:
        session.close()

    assert payload["verified"] is True
    assert payload["input_fallback"]["used"] is True
    assert payload["input_fallback"]["expected_text"] == "165"
    assert captured["verify_texts"] == ["165"]
    assert [item["label"] for item in captured["clicks"]] == [
        "Clear", "8", "6", "Plus", "7", "9", "Equals",
    ]


def test_type_text_schema_exposes_keyboard_mode_and_keypad_guidance() -> None:
    from winpilot.agent.tool_schemas import tool_schemas

    type_tool = next(
        tool["function"]
        for tool in tool_schemas()
        if tool["function"]["name"] == "type_text"
    )

    keyboard_mode = type_tool["parameters"]["properties"]["keyboard_mode"]
    assert set(keyboard_mode["enum"]) == {"default", "calculation", "numeric_keypad"}
    assert "keypad" in type_tool["description"].casefold()


def test_unknown_mcp_server_does_not_fall_back_to_querying_every_server(monkeypatch) -> None:
    from winpilot.integrations import mcp_client

    monkeypatch.setattr(mcp_client, "configured_servers", lambda: {"known": {}})
    result = mcp_client.discover(server_name="missing")

    assert result["queried_server_count"] == 0
    assert result["tools"] == []
