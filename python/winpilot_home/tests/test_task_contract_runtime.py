from __future__ import annotations

from pathlib import Path
import time

import pytest


TASKS = (
    "用画板画一只猫然后截图用QQ发给我的手机",
    "把刚画的猫截图发送到QQ的‘我的手机’",
    "在画图里画猫，截取画图窗口，通过QQ发给我的手机",
)


def _compile(task: str, *, cold_start: bool = False):
    from winpilot.tasking.compiler import TaskContractCompiler

    return TaskContractCompiler().compile_graph(task, cold_start=cold_start)


def test_structured_intent_treats_send_synonyms_equally():
    from winpilot.tasking.intent import TaskIntentParser

    parser = TaskIntentParser()
    variants = (
        "把截图用QQ发给我的手机",
        "把截图用QQ发送给我的手机",
        "通过QQ给我的手机发截图",
        "把截图传给QQ里的我的手机",
    )
    intents = [parser.parse(item) for item in variants]
    assert all("send" in item.operation for item in intents)
    assert all(item.media_type == "image" for item in intents)
    assert all(item.channel == "QQ" for item in intents)
    assert all(item.recipient == "我的手机" for item in intents)


def test_structured_intent_extracts_wechat_destination_relation():
    from winpilot.tasking.intent import TaskIntentParser

    intent = TaskIntentParser().parse("把图片发到微信文件传输助手")
    assert intent.operation == ["send"]
    assert intent.media_type == "image"
    assert intent.channel == "WeChat"
    assert intent.recipient == "文件传输助手"


def test_structured_intent_owns_chat_execution_mode():
    from winpilot.tasking.intent import TaskIntentParser

    parser = TaskIntentParser()

    assert parser.parse("keep chatting with Alice continuously").send_mode == "continuous_chat"
    assert parser.parse("chat with Alice").send_mode == "bounded_chat"
    assert parser.parse("send a message to Alice").send_mode == "send"


def test_text_send_compiles_payload_and_typed_stages():
    contract, _graph = _compile("用QQ给测试联系人发消息“你好”")
    assert [stage.type.value for stage in contract.stages] == [
        "OpenConversation", "SendText", "VerifyDelivery",
    ]
    send = contract.stages[1]
    assert send.parameters["text"] == "你好"


def test_cross_application_artifact_tasks_compile_to_typed_stages():
    contracts = [_compile(task)[0] for task in TASKS]
    for contract in contracts:
        types = [stage.type.value for stage in contract.stages]
        assert "CaptureArtifact" in types
        assert types[-3:] == ["OpenConversation", "SendArtifact", "VerifyDelivery"]
        assert len(types) >= 4
        assert contract.intent.channel == "QQ"
        assert contract.intent.recipient == "我的手机"
        assert contract.intent.media_type == "image"
        assert contract.satisfiable is True
    assert [stage.type.value for stage in contracts[0].stages] == [
        "DrawArtifact", "CaptureArtifact", "OpenConversation", "SendArtifact", "VerifyDelivery",
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
    draw = next(stage for stage in contract.stages if stage.type.value == "DrawArtifact")
    profile = build_runtime_profile("Draw the requested subject", task_contract=contract, stage=draw)
    assert profile.experience_enabled is False
    assert profile.replay is False


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


def test_delivery_selects_deterministic_evidence_without_semantic_model():
    contract, _graph = _compile(TASKS[0])
    stage = next(item for item in contract.stages if item.type.value == "VerifyDelivery")
    selection = stage.execution
    assert selection is not None
    assert selection.selected_evidence_providers == {
        "delivery.visible_outbound": "communication_adapter_visible_outbound",
    }
    assert "visual.semantic_verify" not in selection.verifier_requirements
    assert selection.model_role == "none"


def test_draw_stage_exposes_controller_transaction_and_bounded_timeout():
    contract, graph = _compile(TASKS[0])
    stage = next(item for item in contract.stages if item.type.value == "DrawArtifact")
    assert stage.execution is not None
    assert {"execute_subgoal", "run_action_batch", "draw_strokes"} <= set(stage.execution.tools)
    assert "draw_strokes" in stage.execution.controller_tools
    assert "draw_strokes" not in stage.execution.model_tools
    assert "execute_subgoal" in stage.execution.model_tools
    node = next(item for item in graph.nodes if item.id == stage.id)
    assert node.timeout_s == 180


def test_semantic_child_intent_is_scoped_to_its_typed_stage():
    from winpilot.tasking.stage_runtime import StageRuntime

    contract, _graph = _compile(TASKS[0], cold_start=True)
    stage = next(item for item in contract.stages if item.type.value == "DrawArtifact")
    goal = "In the already opened Paint, draw the compiler-resolved subject."

    intent = StageRuntime._stage_scoped_intent(contract, stage, goal)
    communication = StageRuntime._stage_communication_context(stage, intent)

    assert intent.original_text == goal
    assert intent.operation == ["draw"]
    assert intent.subject == contract.intent.subject
    assert intent.quality == contract.intent.quality
    assert intent.source == stage.parameters["app"]
    assert intent.channel == ""
    assert intent.recipient == ""
    assert intent.destination == ""
    assert intent.interaction.active is False
    assert intent.provenance["stage_scope"] == f"{contract.task_id}:{stage.id}"
    assert communication == {
        "active": False,
        "recipient": "",
        "channel": "",
        "send_mode": "send",
    }


def test_semantic_child_runner_never_uses_raw_intent_parser_fallback(monkeypatch):
    import threading

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.router import build_runner as real_build_runner
    from winpilot.tasking.intent import TaskIntentParser
    from winpilot.tasking.stage_runtime import StageRuntime

    contract, _graph = _compile(TASKS[0], cold_start=True)
    stage = next(item for item in contract.stages if item.type.value == "DrawArtifact")
    stage = stage.model_copy(deep=True)
    # This test exercises only semantic-child construction; application launch
    # is a separate deterministic stage concern.
    stage.parameters = {**stage.parameters, "app": ""}

    def parser_fallback_is_a_bug(*_args, **_kwargs):
        raise AssertionError("semantic child reparsed its generated raw goal")

    monkeypatch.setattr(TaskIntentParser, "parse", parser_fallback_is_a_bug)

    def successful_semantic_run(runner):
        runner.status = "done"
        runner.result_text = "semantic child complete"
        runner._done_verified = True
        runner._inline_stage_semantic_attempted = True
        runner._inline_stage_semantic_passed = True
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
        assert scoped.operation == ["draw"]
        assert scoped.channel == ""
        assert scoped.recipient == ""
        assert captured["kwargs"]["communication_context"]["active"] is False
        assert captured["runner"].task_intent["operation"] == ["draw"]
    finally:
        runner = captured.get("runner")
        if runner is not None:
            runner.session.close()


def test_draw_boundary_requires_a_real_drawing_mutation():
    from winpilot.tasking.stage_runtime import StageRuntime

    assert StageRuntime._has_successful_draw_mutation([
        ("execute_subgoal", {"subgoal": "focus canvas"}, '{"ok": true, "verified": true}'),
    ]) is False
    assert StageRuntime._has_successful_draw_mutation([
        (
            "execute_subgoal",
            {"subgoal": "draw requested subject"},
            '{"ok": true, "evidence_packet": {"scene_structure": '
            '{"executed_draw_count": 3, "structured_scene_verified": true}}}',
        ),
    ]) is True
    assert StageRuntime._has_successful_draw_mutation([
        ("draw_strokes", {"frame_strokes": [[[10, 10], [20, 20]]]}, '{"ok": true}'),
    ]) is True


def test_draw_boundary_uses_fresh_bound_window_semantic_verifier(monkeypatch):
    import json

    from winpilot.agent.tool_result import ToolResult
    from winpilot.tasking.stage_runtime import StageRuntime

    observed = {}

    class FakeSession:
        def __init__(self, *, stop_event, task, **_kwargs):
            observed["task"] = task
            observed["stop_event"] = stop_event
            self.run_id = ""

        def _remember_target(self, hwnd, app):
            observed["target"] = (hwnd, app)

        def dispatch(self, name, args):
            observed["dispatch"] = (name, args)
            return ToolResult(json.dumps({
                "ok": True,
                "verified": True,
                "semantic_visual_verified": True,
                "verify_method": "semantic-vlm",
            }))

        def close(self):
            observed["closed"] = True

    monkeypatch.setattr("winpilot.tasking.stage_runtime.ToolSession", FakeSession)
    verdict = StageRuntime()._verify_draw_boundary(
        goal="draw a bird", subject="bird", quality="normal", app="Paint",
        hwnd=42, run_id="run-boundary", stop_event=object(), stage_id="draw-1",
    )
    assert verdict["semantic_visual_verified"] is True
    assert observed["target"] == (42, "Paint")
    assert observed["dispatch"][0] == "vlm_verify"
    assert "recognizable bird" in observed["dispatch"][1]["criteria"]
    assert observed["closed"] is True


def test_typed_stage_guidance_is_not_part_of_visible_completion_task():
    from winpilot.agent.done_verifier import _required_literal_tokens
    from winpilot.agent.runtime_profile import build_runtime_profile

    semantic_goal = "In the already opened Paint, draw a bird. The visible result must be clearly recognizable."
    guidance = "Prefer execute_subgoal with normalized_strokes in canvas-relative 0..1000 geometry."
    profile = build_runtime_profile(semantic_goal, stage_guidance=guidance)
    assert guidance in profile.prompt_section()
    assert not {"execute_subgoal", "normalized_strokes", "1000"} & set(
        _required_literal_tokens(semantic_goal)
    )


def test_draw_stage_model_schema_requires_structured_scene_plan():
    from winpilot.agent.runtime_profile import build_runtime_profile
    from winpilot.agent.tool_schemas import tool_schemas

    contract, _graph = _compile(TASKS[0])
    stage = next(item for item in contract.stages if item.type.value == "DrawArtifact")
    profile = build_runtime_profile(
        "Draw the requested subject",
        task_contract=contract,
        stage=stage,
        model_tool_allowlist=("execute_subgoal", "vlm_verify", "done", "blocked", "fail"),
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
    )
    execute = next(item for item in schemas if item["function"]["name"] == "execute_subgoal")
    parameters = execute["function"]["parameters"]
    assert parameters["required"] == ["subgoal", "draws"]
    assert "draw" not in parameters["properties"]
    draw_item = parameters["properties"]["draws"]["items"]
    assert "shared silhouette" in parameters["properties"]["draws"]["description"]
    assert draw_item["required"] == ["part"]
    assert draw_item["anyOf"][0]["required"] == ["part", "shape", "box", "box_mode"]
    assert draw_item["properties"]["box_mode"]["enum"] == ["endpoints"]
    assert "draw_strokes" not in profile.model_tool_allowlist


def test_inline_stage_semantic_gate_requires_executed_draw_and_fresh_verdict(monkeypatch):
    import json

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.runtime_profile import RuntimeProfile
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner(
        "Draw a bird",
        runtime_profile=RuntimeProfile(inline_semantic_verification=True, stage_type="DrawArtifact"),
        max_steps_override=2,
    )
    try:
        assert runner._inline_stage_semantic_verdict(
            "execute_subgoal",
            {"ok": True, "evidence_packet": {"scene_structure": {"executed_draw_count": 0}}},
        ) is None
        monkeypatch.setattr(runner.session, "dispatch", lambda name, args: ToolResult(json.dumps({
            "ok": True,
            "verified": True,
            "semantic_visual_verified": True,
            "verify_method": "semantic-vlm",
        })))
        verdict = runner._inline_stage_semantic_verdict(
            "execute_subgoal",
            {"ok": True, "evidence_packet": {"scene_structure": {"executed_draw_count": 4}}},
        )
        assert verdict and verdict["semantic_visual_verified"] is True
        assert runner._done_verified is True
        assert runner._inline_stage_semantic_passed is True
        assert runner.model_request_count == 1
    finally:
        runner.session.close()


def test_inline_semantic_roi_is_controller_geometry_not_window_or_subject():
    from winpilot.agent.loop import AgentRunner

    payload = {
        "actions_executed": [
            {
                "action": "draw_strokes",
                "draw_region": {"x": 300, "y": 240, "width": 220, "height": 120},
                "canvas_region": {"x": 100, "y": 180, "width": 800, "height": 500},
            },
            {
                "action": "draw_strokes",
                "draw_region": {"x": 490, "y": 300, "width": 180, "height": 160},
                "canvas_region": {"x": 100, "y": 180, "width": 800, "height": 500},
            },
        ],
    }
    region = AgentRunner._inline_semantic_region(payload)
    assert region == {"left": 256, "top": 214, "width": 458, "height": 272}
    assert region["left"] >= 100 and region["top"] >= 180
    assert region["left"] + region["width"] <= 900
    assert region["top"] + region["height"] <= 680


def test_failed_inline_semantic_verdict_drives_one_forward_only_repair():
    from winpilot.agent.loop import AgentRunner

    hint = AgentRunner._inline_stage_repair_hint({
        "semantic_visual_verified": False,
        "verdict": {"issues": ["missing body", "missing legs and tail"]},
    })
    assert "missing body" in hint
    assert "missing legs and tail" in hint
    assert "execute_subgoal once" in hint
    assert "Do not call done" in hint
    assert "shared outer silhouette" in hint
    assert "another complete copy" in hint
    assert "do not clear or redraw" in hint
    assert AgentRunner._inline_stage_repair_hint({"semantic_visual_verified": True}) == ""


def test_typed_draw_repair_rejects_whole_scene_overlay_without_executing_it():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.runtime_profile import RuntimeProfile

    runner = AgentRunner(
        "Draw the requested visible subject",
        runtime_profile=RuntimeProfile(
            inline_semantic_verification=True,
            stage_type="DrawArtifact",
        ),
        max_steps_override=3,
    )
    try:
        base = {
            "draws": [
                {"part": f"part-{index}", "shape": "line", "box": [10, 10, 20, 20]}
                for index in range(12)
            ],
        }
        runner._remember_inline_stage_repair_state(
            "execute_subgoal",
            base,
            {"semantic_visual_verified": False},
        )
        blocked = runner._inline_stage_forward_repair_guard(
            "execute_subgoal",
            {"draws": list(base["draws"])},
        )
        assert blocked is not None
        assert blocked["blocked"] is True
        assert blocked["forward_only_repair_required"] is True
        assert blocked["base_draw_count"] == 12
        assert blocked["max_repair_draws"] == 4
        assert blocked["proposed_draw_count"] == 12

        localized = runner._inline_stage_forward_repair_guard(
            "execute_subgoal",
            {"draws": list(base["draws"][:3])},
        )
        assert localized is None

        runner._remember_inline_stage_repair_state(
            "execute_subgoal",
            {"draws": list(base["draws"][:3])},
            {"semantic_visual_verified": True},
        )
        assert runner._inline_stage_repair_pending is False
    finally:
        runner.session.close()


def test_failed_typed_stage_semantic_verdict_cannot_be_overridden_by_done_judge():
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.runtime_profile import RuntimeProfile

    runner = AgentRunner(
        "Draw a bird",
        runtime_profile=RuntimeProfile(inline_semantic_verification=True, stage_type="DrawArtifact"),
        max_steps_override=2,
    )
    try:
        runner._inline_stage_semantic_attempted = True
        runner._inline_stage_semantic_passed = False
        runner._inline_stage_semantic_last_verdict = {
            "verdict": {"issues": ["subject is not recognizable"]},
        }
        assert runner._verify_done(client=None) is False
        assert "fresh-frame semantic verifier rejected" in runner._last_done_reject_reason
    finally:
        runner.session.close()


def test_native_vision_send_stage_exposes_artifact_capture_send_and_verify_tools():
    from winpilot.agent.tool_schemas import tool_schemas

    contract, _graph = _compile(TASKS[0])
    stage = next(item for item in contract.stages if item.type.value == "SendArtifact")
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
    assert {"capture_artifact", "send_artifact", "verify_delivery", "paste_image_file", "read_text"} <= names


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
    artifact = store.capture_artifact(scope="screen", source_window="Paint")
    path = Path(artifact.managed_path)
    assert artifact.run_id == "run-artifact"
    assert path.is_file() and path.stat().st_size > 0
    assert artifact.created_at >= started
    assert artifact.source_window == "Paint"
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
    assert StageRuntime._resolve_hwnd("Paint") == 0


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


def test_chat_adapter_causally_binds_new_single_attachment_when_thumbnail_is_lossy(tmp_path):
    import json
    from types import SimpleNamespace

    from PIL import Image, ImageDraw

    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    source_path = tmp_path / "full-window-artifact.png"
    source = Image.new("RGB", (1008, 720), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 1007, 150), fill="#202020")
    draw.ellipse((360, 280, 700, 560), outline="black", width=12)
    source.save(source_path)
    before_path = tmp_path / "chat-before.png"
    Image.new("RGB", (800, 600), "#202020").save(before_path)
    after_path = tmp_path / "chat-confirmation.png"
    after = Image.open(before_path).copy()
    ImageDraw.Draw(after).rectangle((292, 162, 508, 472), fill="#dddddd")
    after.save(after_path)

    store = ArtifactStore("run-causal-preview", tmp_path / "managed-causal")
    artifact = store.import_file(source_path)
    frame_metadata = {"region": {"left": 0, "top": 0, "width": 800, "height": 600}}
    before_frame = store.import_file(
        before_path,
        purpose="observation",
        sendable=False,
        metadata=frame_metadata,
    )
    after_frame = store.import_file(
        after_path,
        purpose="observation",
        sendable=False,
        metadata=frame_metadata,
    )
    adapter = QQCommunicationAdapter(
        run_id="run-causal-preview",
        task="send the current image to a recipient",
        store=store,
        evidence=EvidenceRegistry("run-causal-preview"),
    )
    adapter.session.close()
    adapter.recipient = "测试联系人"
    before_snapshot = SimpleNamespace(region=Region(0, 0, 800, 600), elements=[])
    after_snapshot = SimpleNamespace(region=Region(0, 0, 800, 600), elements=[])
    receipt = {
        "ok": True,
        "artifact_id": artifact.artifact_id,
        "sha256": artifact.sha256,
        "width": artifact.width,
        "height": artifact.height,
        "via": "clipboard_dib",
        "delivery": False,
        "preview_only": True,
    }

    class FakeSession:
        hwnd = 1
        snapshot = before_snapshot

        def dispatch(self, name, _args):
            assert name == "paste_image_file"
            return SimpleNamespace(text=json.dumps(receipt))

        def close(self):
            return None

    adapter.session = FakeSession()
    frames = iter((before_frame, after_frame))
    adapter._capture_observation = lambda _purpose: next(frames)
    adapter._observe_snapshot = lambda: after_snapshot
    adapter._recipient_header_context = lambda _snapshot: {"verified": True, "recipient": adapter.recipient}
    adapter._attachment_confirmation_context = lambda snapshot: (
        {
            "verified": True,
            "recipient": adapter.recipient,
            "attachment_count": 1,
            "send_button": {"element_id": 9, "bbox": [380, 420, 80, 30]},
        }
        if snapshot is after_snapshot
        else {"verified": False, "recipient": adapter.recipient, "attachment_count": 0}
    )
    original_visual_match = store.visual_match
    match_calls = 0

    def weak_composer_match(*_args, **_kwargs):
        nonlocal match_calls
        match_calls += 1
        if match_calls == 1:
            return {
                "matched": True,
                "method": "multiscale_edge_template",
                "template_score": 0.46,
                "template_scale": 0.04,
                "match_bbox": [760, 580, 32, 16],
            }
        return {"matched": False, "reason": "lossy tiny confirmation thumbnail"}

    store.visual_match = weak_composer_match
    try:
        result = adapter.paste_or_attach_artifact(artifact.artifact_id, stage_id="stage-send")
    finally:
        store.visual_match = original_visual_match
        adapter.close()

    assert result["ok"] is True, result
    assert result["preview_surface"] == "attachment_confirmation"
    assert result["preview_match"]["method"] == "exact_paste_receipt+new_single_attachment_confirmation"
    assert result["preview_match"]["frame_change"]["changed"] is True
    assert result["preview_match"]["visual_match"]["skipped"] == "causal_attachment_confirmation"
    assert match_calls == 0
    assert adapter._pending_send_confirmation["recipient"] == "测试联系人"
    assert adapter._pending_send_confirmation["artifact_id"] == artifact.artifact_id
    assert adapter._pending_send_confirmation["artifact_sha256"] == artifact.sha256


def test_chat_adapter_consumes_only_matching_fresh_confirmation_transaction(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from PIL import Image

    from winpilot.tasking import adapters as adapter_module
    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    source_path = tmp_path / "artifact.png"
    Image.new("RGB", (160, 100), "white").save(source_path)
    store = ArtifactStore("run-confirm-send", tmp_path / "managed-confirm-send")
    artifact = store.import_file(source_path)
    evidence = EvidenceRegistry("run-confirm-send")
    adapter = QQCommunicationAdapter(
        run_id="run-confirm-send",
        task="send the current artifact to a recipient",
        store=store,
        evidence=evidence,
    )
    adapter.session.close()
    adapter.recipient = "任意联系人"
    confirm_snapshot = SimpleNamespace(region=Region(0, 0, 800, 600), elements=[])
    chat_snapshot = SimpleNamespace(region=Region(0, 0, 800, 600), elements=[])
    dispatched = []

    class FakeSession:
        hwnd = 1
        snapshot = confirm_snapshot

        def dispatch(self, name, args):
            dispatched.append((name, dict(args)))
            return SimpleNamespace(text=json.dumps({"ok": True}))

        def close(self):
            return None

    adapter.session = FakeSession()
    snapshots = iter((confirm_snapshot, chat_snapshot))
    adapter._observe_snapshot = lambda: next(snapshots)
    adapter._attachment_confirmation_context = lambda snapshot: (
        {
            "verified": True,
            "recipient": adapter.recipient,
            "attachment_count": 1,
            "send_button": {
                "element_id": 22,
                "source": "ocr",
                "bbox": [350, 450, 100, 40],
            },
        }
        if snapshot is confirm_snapshot else {"verified": False}
    )
    adapter._recipient_header_context = lambda snapshot: {
        "verified": snapshot is chat_snapshot,
        "recipient": adapter.recipient,
    }
    adapter._pending_send_confirmation = {
        "verified": True,
        "recipient": adapter.recipient,
        "attachment_count": 1,
        "artifact_id": artifact.artifact_id,
        "artifact_sha256": artifact.sha256,
        "send_button": {"element_id": 9, "source": "ocr", "bbox": [1, 2, 3, 4]},
    }
    adapter._pre_paste_frame_id = "clean-pre-paste-frame"
    monkeypatch.setattr(adapter_module.time, "sleep", lambda _seconds: None)
    try:
        sent = adapter.send_current_composer(artifact_id=artifact.artifact_id, stage_id="stage-send")
    finally:
        adapter.close()

    assert sent["ok"] is True, sent
    assert dispatched == [("click", {"x": 400, "y": 470, "physical": True})]
    assert sent["send_action"]["metadata"]["action"] == "attachment_confirmation_button"
    assert sent["send_action"]["metadata"]["attachment_confirmation"]["artifact_id"] == artifact.artifact_id
    assert adapter._pending_send_confirmation == {}


def test_chat_adapter_rejects_confirmation_for_different_artifact(tmp_path):
    from PIL import Image

    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry

    source_path = tmp_path / "artifact.png"
    Image.new("RGB", (80, 60), "white").save(source_path)
    store = ArtifactStore("run-confirm-mismatch", tmp_path / "managed-confirm-mismatch")
    artifact = store.import_file(source_path)
    adapter = QQCommunicationAdapter(
        run_id="run-confirm-mismatch",
        task="send an artifact",
        store=store,
        evidence=EvidenceRegistry("run-confirm-mismatch"),
    )
    adapter.recipient = "任意联系人"
    adapter._pending_send_confirmation = {
        "verified": True,
        "recipient": adapter.recipient,
        "artifact_id": artifact.artifact_id,
        "artifact_sha256": artifact.sha256,
    }
    try:
        sent = adapter.send_current_composer(artifact_id="art-some-other-current-run-item")
    finally:
        adapter.close()

    assert sent["ok"] is False
    assert sent["failure_type"] == "artifact_identity_unproven"


def test_chat_adapter_rejects_stale_attachment_confirmation_before_paste(tmp_path):
    from types import SimpleNamespace

    from PIL import Image

    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry

    source_path = tmp_path / "artifact.png"
    Image.new("RGB", (120, 80), "white").save(source_path)
    store = ArtifactStore("run-stale-confirm", tmp_path / "managed-stale")
    artifact = store.import_file(source_path)
    adapter = QQCommunicationAdapter(
        run_id="run-stale-confirm",
        task="send an artifact",
        store=store,
        evidence=EvidenceRegistry("run-stale-confirm"),
    )
    adapter.session.close()

    class FakeSession:
        hwnd = 1
        snapshot = SimpleNamespace()

        def dispatch(self, name, _args):
            raise AssertionError(f"stale confirmation must block {name}")

        def close(self):
            return None

    adapter.session = FakeSession()
    adapter.recipient = "任意联系人"
    adapter._recipient_header_context = lambda _snapshot: {"verified": True}
    adapter._attachment_confirmation_context = lambda _snapshot: {
        "verified": True,
        "recipient": adapter.recipient,
        "attachment_count": 1,
    }
    try:
        result = adapter.paste_or_attach_artifact(artifact.artifact_id)
    finally:
        adapter.close()
    assert result["ok"] is False
    assert result["failure_type"] == "stale_confirmation_visible"


def test_visual_match_requires_new_changed_candidate_inside_delivery_region(tmp_path):
    from PIL import Image, ImageDraw

    from winpilot.tasking.artifacts import ArtifactStore

    source_path = tmp_path / "artifact.png"
    source = Image.new("RGB", (240, 160), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((25, 30, 215, 130), outline="black", width=8)
    draw.line((30, 120, 120, 40, 210, 120), fill="black", width=7)
    source.save(source_path)
    baseline_path = tmp_path / "baseline.png"
    baseline = Image.new("RGB", (800, 600), "#222222")
    baseline.save(baseline_path)
    post_path = tmp_path / "post.png"
    post = baseline.copy()
    post.paste(source.resize((120, 80)), (520, 300))
    post.save(post_path)

    store = ArtifactStore("run-delta-match", tmp_path / "managed")
    artifact = store.import_file(source_path)
    baseline_artifact = store.import_file(baseline_path, purpose="observation", sendable=False)
    post_artifact = store.import_file(post_path, purpose="observation", sendable=False)
    region = {"left": 400, "top": 180, "width": 350, "height": 300}
    match = store.visual_match(
        artifact.artifact_id,
        post_artifact.artifact_id,
        target_region=region,
        baseline_frame_id=baseline_artifact.artifact_id,
        require_changed=True,
    )
    assert match["matched"] is True, match
    assert match["changed_after_send"] is True
    assert match["match_bbox"][0] >= region["left"]
    outside = store.visual_match(
        artifact.artifact_id,
        post_artifact.artifact_id,
        target_region={"left": 0, "top": 0, "width": 300, "height": 180},
        baseline_frame_id=baseline_artifact.artifact_id,
        require_changed=True,
    )
    assert outside["matched"] is False


def test_chat_adapter_requires_recipient_in_header_not_contact_list(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from winpilot.perception.model import UIElement
    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    adapter = QQCommunicationAdapter(
        run_id="run-layout",
        task="send an image",
        store=ArtifactStore("run-layout", tmp_path / "artifacts"),
        evidence=EvidenceRegistry("run-layout"),
    )
    adapter.recipient = "测试联系人"
    monkeypatch.setattr("winpilot.tasking.adapters.ocr.collect", lambda _region, min_conf=None: [])
    contact = UIElement(elem_id=1, source="uia", role="text", text="测试联系人", cx=180, cy=140, left=140, top=128, width=80, height=24)
    snapshot = SimpleNamespace(region=Region(0, 0, 1920, 1020), elements=[contact])
    try:
        assert adapter._recipient_header_context(snapshot)["verified"] is False
        header = UIElement(elem_id=2, source="uia", role="text", text="测试联系人", cx=420, cy=95, left=380, top=83, width=80, height=24)
        snapshot.elements.append(header)
        context = adapter._recipient_header_context(snapshot)
        assert context["verified"] is True
        assert context["element"]["element_id"] == 2
    finally:
        adapter.close()


def test_chat_adapter_uses_targeted_ocr_when_uia_omits_visible_header(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from winpilot.perception.model import UIElement
    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    adapter = QQCommunicationAdapter(
        run_id="run-header-ocr",
        task="send an image",
        store=ArtifactStore("run-header-ocr", tmp_path / "artifacts-header-ocr"),
        evidence=EvidenceRegistry("run-header-ocr"),
    )
    adapter.recipient = "任意联系人"
    contact = UIElement(
        elem_id=1, source="uia", role="text", text="任意联系人",
        cx=180, cy=140, left=140, top=128, width=80, height=24,
    )
    header = UIElement(
        elem_id=2, source="ocr", role="text", text="任意联系人",
        cx=420, cy=95, left=380, top=83, width=80, height=24,
    )
    requested_regions = []

    def collect(region, min_conf=None):
        requested_regions.append(region)
        assert min_conf == 0.45
        return [header]

    monkeypatch.setattr("winpilot.tasking.adapters.ocr.collect", collect)
    snapshot = SimpleNamespace(region=Region(0, 0, 1920, 1020), elements=[contact])
    try:
        context = adapter._recipient_header_context(snapshot)
    finally:
        adapter.close()

    assert context["verified"] is True
    assert context["element"]["source"] == "ocr"
    assert len(requested_regions) == 1
    expected = adapter.layout_profile.region(snapshot.region, adapter.layout_profile.recipient_header)
    assert requested_regions[0] == expected
    full_header = adapter.layout_profile.region(snapshot.region, adapter.layout_profile.header)
    assert expected.width < full_header.width


def test_chat_adapter_does_not_reclick_recipient_when_correct_conversation_is_open(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from winpilot.perception.model import UIElement
    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    adapter = QQCommunicationAdapter(
        run_id="run-already-open",
        task="open the recipient conversation",
        store=ArtifactStore("run-already-open", tmp_path / "artifacts-already-open"),
        evidence=EvidenceRegistry("run-already-open"),
    )
    adapter.session.close()
    recipient = "任意联系人"
    header = UIElement(
        elem_id=2, source="ocr", role="text", text=recipient,
        cx=420, cy=95, left=380, top=83, width=80, height=24,
    )
    snapshot = SimpleNamespace(region=Region(0, 0, 1920, 1020), elements=[header])
    dispatched = []

    class FakeSession:
        def __init__(self, current_snapshot):
            self.hwnd = 123
            self.snapshot = current_snapshot

        def dispatch(self, name, args):
            dispatched.append((name, args))
            if name not in {"open_app", "hotkey"}:
                raise AssertionError(f"already-open conversation must not dispatch {name}")
            return SimpleNamespace(text=json.dumps({"ok": True}))

        def close(self):
            return None

    adapter.session = FakeSession(snapshot)
    adapter._observe_snapshot = lambda: snapshot
    adapter._dismiss_stale_attachment_confirmation = lambda _snapshot: {
        "ok": True,
        "dismissed": False,
    }
    monkeypatch.setattr("winpilot.tasking.adapters.ocr.collect", lambda _region, min_conf=None: [])
    try:
        result = adapter.open_conversation(recipient, stage_id="stage-open")
    finally:
        adapter.close()

    assert result["ok"] is True
    assert result["strategy"] == "already_open"
    assert [name for name, _args in dispatched] == ["open_app", "hotkey"]


def test_chat_adapter_binds_recipient_and_send_button_in_attachment_dialog(tmp_path):
    from types import SimpleNamespace

    from winpilot.perception.model import UIElement
    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    adapter = QQCommunicationAdapter(
        run_id="run-confirm",
        task="send an image",
        store=ArtifactStore("run-confirm", tmp_path / "artifacts-confirm"),
        evidence=EvidenceRegistry("run-confirm"),
    )
    adapter.recipient = "测试联系人"
    snapshot = SimpleNamespace(
        region=Region(0, 0, 1920, 1020),
        elements=[
            UIElement(elem_id=8, source="ocr", role="text", text="发送给测试联系人", cx=960, cy=290, left=880, top=278, width=160, height=24),
            UIElement(elem_id=9, source="uia", role="button", text="发送(1)", cx=960, cy=700, left=910, top=680, width=100, height=40),
            UIElement(elem_id=10, source="uia", role="button", text="发送", cx=1820, cy=980, left=1770, top=960, width=100, height=40),
        ],
    )
    try:
        context = adapter._attachment_confirmation_context(snapshot)
        assert context["verified"] is True
        assert context["recipient_element"]["element_id"] == 8
        assert context["send_button"]["element_id"] == 9
    finally:
        adapter.close()


def test_chat_adapter_dismisses_proven_stale_confirmation_with_verified_fallback(tmp_path):
    import json
    from types import SimpleNamespace

    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    adapter = QQCommunicationAdapter(
        run_id="run-dismiss-stale",
        task="open a conversation",
        store=ArtifactStore("run-dismiss-stale", tmp_path / "artifacts-dismiss"),
        evidence=EvidenceRegistry("run-dismiss-stale"),
    )
    adapter.session.close()
    adapter.recipient = "任意联系人"
    stale = SimpleNamespace(region=Region(0, 0, 1920, 1020), elements=[])
    clear = SimpleNamespace(region=Region(0, 0, 1920, 1020), elements=[])
    dispatched = []

    class FakeSession:
        def dispatch(self, name, args):
            dispatched.append((name, args))
            return SimpleNamespace(text=json.dumps({"ok": True}))

        def close(self):
            return None

    adapter.session = FakeSession()
    adapter._attachment_confirmation_context = lambda snapshot: (
        {"verified": True, "recipient": adapter.recipient, "attachment_count": 1}
        if snapshot is stale
        else {"verified": False, "recipient": adapter.recipient, "attachment_count": 0}
    )
    observations = iter((stale, clear))
    adapter._observe_snapshot = lambda: next(observations)
    try:
        result = adapter._dismiss_stale_attachment_confirmation(stale)
    finally:
        adapter.close()

    assert result["ok"] is True
    assert result["dismissed"] is True
    assert [name for name, _args in dispatched] == ["hotkey", "click"]
    click = dispatched[1][1]
    dismiss_region = adapter.layout_profile.region(stale.region, adapter.layout_profile.attachment_dismiss)
    assert dismiss_region.left <= click["x"] <= dismiss_region.right
    assert dismiss_region.top <= click["y"] <= dismiss_region.bottom
    assert click["physical"] is True


def test_completion_rejects_preview_without_real_send_or_visible_outbound(tmp_path, monkeypatch):
    import numpy as np

    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.completion import CompletionContractVerifier
    from winpilot.tasking.evidence import EvidenceRegistry

    contract, _graph = _compile(TASKS[0])
    monkeypatch.setattr("winpilot.tasking.artifacts.capture", lambda _region=None: np.zeros((16, 16, 3), dtype=np.uint8))
    store = ArtifactStore("run-preview", tmp_path)
    artifact = store.capture_artifact(scope="screen", source_window="Paint")
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
    assert {item["obligation_id"] for item in verdict["missing"]} >= {
        "delivery.real_send_action", "delivery.visible_outbound",
    }


def test_completion_requires_same_artifact_across_capture_send_and_delivery(tmp_path, monkeypatch):
    import numpy as np

    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.completion import CompletionContractVerifier
    from winpilot.tasking.evidence import EvidenceRegistry

    contract, _graph = _compile(TASKS[0])
    monkeypatch.setattr("winpilot.tasking.artifacts.capture", lambda _region=None: np.zeros((16, 16, 3), dtype=np.uint8))
    store = ArtifactStore("run-identity", tmp_path)
    primary = store.capture_artifact(scope="screen", source_window="Paint")
    unrelated = store.capture_artifact(scope="screen", source_window="Paint")
    evidence = EvidenceRegistry("run-identity")
    evidence.record("artifact.current_run_valid", provider="artifact_store_capture", verified=True, artifact_id=primary.artifact_id)
    evidence.record("conversation.correct_recipient", provider="communication_adapter_context", verified=True, recipient="我的手机")
    evidence.record("delivery.real_send_action", provider="communication_adapter_send", verified=True, artifact_id=unrelated.artifact_id, recipient="我的手机")
    evidence.record("delivery.visible_outbound", provider="communication_adapter_visible_outbound", verified=True, artifact_id=unrelated.artifact_id, recipient="我的手机")
    verdict = CompletionContractVerifier(contract, evidence, store).verify()
    assert verdict["ok"] is False
    assert {item["obligation_id"] for item in verdict["missing"]} >= {
        "delivery.real_send_action", "delivery.visible_outbound",
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
    from winpilot.tasking.contracts import StageType

    first, _ = _compile(TASKS[0])
    third, _ = _compile(TASKS[2])
    first_capture = next(stage for stage in first.stages if stage.type == StageType.CAPTURE_ARTIFACT)
    third_capture = next(stage for stage in third.stages if stage.type == StageType.CAPTURE_ARTIFACT)
    assert first_capture.parameters["scope"] == "canvas"
    assert third_capture.parameters["scope"] == "window"


def test_stage_skill_graph_resumes_at_failed_stage_instead_of_whole_task():
    _contract, graph = _compile(TASKS[0])
    draw = next(node for node in graph.nodes if node.kind.value == "task_stage" and node.config["stage"]["type"] == "DrawArtifact")
    skill = next(node for node in graph.nodes if node.kind.value == "skill_replay" and node.config.get("stage_id") == draw.id)
    routes = {(edge.branch, edge.target) for edge in graph.edges if edge.source == skill.id}
    assert ("miss", draw.id) in routes
    assert ("fail", draw.id) in routes
    assert skill.config["resume_on_failure"] == draw.id
    assert skill.config["parameters"]["subject"]


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
            "parameters": {"app": "Paint"}, "outputs": [{"name": "visual_content"}],
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
        "DrawArtifact", {"app": "Paint", "subject": "bird"}, app_version="11.0",
    )
    assert script == script_name
    assert matched and matched["id"] == "skill-1"
    assert experience.reusable_stage_script_for(
        "DrawArtifact", {"app": "Whiteboard"}, app_version="11.0",
    )[0] == ""
    assert experience.reusable_stage_script_for(
        "DrawArtifact", {"app": "Paint"}, app_version="12.0",
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


def test_communication_adapter_requests_controller_structured_observation(tmp_path):
    from types import SimpleNamespace

    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry

    adapter = QQCommunicationAdapter(
        run_id="run-structured-observe",
        task="send an artifact through a chat client",
        store=ArtifactStore("run-structured-observe", tmp_path / "artifacts"),
        evidence=EvidenceRegistry("run-structured-observe"),
    )
    adapter.session.close()
    marker = object()

    class FakeSession:
        snapshot = None

        def dispatch(self, name, args):
            assert name == "observe"
            assert args == {
                "full": True,
                "include_icons": False,
                "_controller_perception_mode": "structured_ui",
            }
            self.snapshot = marker
            return SimpleNamespace(text='{"ok": true}')

        def close(self):
            return None

    adapter.session = FakeSession()
    assert adapter._observe_snapshot() is marker


def test_resolve_composer_reuses_fresh_verified_navigation_snapshot(tmp_path):
    import json
    from types import SimpleNamespace

    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    recipient = SimpleNamespace(
        elem_id=7, source="uia", role="text", text="target recipient",
        left=250, top=70, width=120, height=30, cx=310, cy=85,
    )
    composer = SimpleNamespace(
        elem_id=8, source="uia", role="edit", text="",
        left=320, top=680, width=500, height=70, cx=570, cy=715,
    )
    snapshot = SimpleNamespace(
        region=Region(0, 0, 1000, 800), elements=[recipient, composer],
    )
    calls: list[tuple[str, dict]] = []

    class FakeSession:
        hwnd = 44

        def __init__(self, initial_snapshot):
            self.snapshot = initial_snapshot

        def dispatch(self, name, args):
            calls.append((name, args))
            assert name != "observe"
            return SimpleNamespace(text=json.dumps({"ok": True}))

        def close(self):
            return None

    adapter = QQCommunicationAdapter(
        run_id="run-composer-cache",
        task="send an artifact through a chat client",
        store=ArtifactStore("run-composer-cache", tmp_path / "artifacts"),
        evidence=EvidenceRegistry("run-composer-cache"),
    )
    adapter.session.close()
    adapter.session = FakeSession(snapshot)
    adapter.recipient = "target recipient"

    result = adapter.resolve_composer()

    assert result["ok"] is True
    assert result["method"] == "uia_composer"
    assert calls == [("click", {"element_id": 8})]


def test_recipient_proof_is_exact_not_substring(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from winpilot.perception.model import UIElement
    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    adapter = QQCommunicationAdapter(
        run_id="run-exact-recipient",
        task="send a message to a recipient",
        store=ArtifactStore("run-exact-recipient", tmp_path / "artifacts"),
        evidence=EvidenceRegistry("run-exact-recipient"),
    )
    adapter.recipient = "Ann"
    monkeypatch.setattr("winpilot.tasking.adapters.ocr.collect", lambda *_args, **_kwargs: [])
    wrong = UIElement(
        elem_id=1, source="uia", role="text", text="Joann",
        left=250, top=70, width=90, height=30, cx=295, cy=85,
    )
    exact = UIElement(
        elem_id=2, source="uia", role="text", text="Ann",
        left=250, top=70, width=70, height=30, cx=285, cy=85,
    )
    snapshot = SimpleNamespace(region=Region(0, 0, 1000, 800), elements=[wrong])
    try:
        assert adapter._recipient_header_context(snapshot)["verified"] is False
        snapshot.elements.append(exact)
        context = adapter._recipient_header_context(snapshot)
    finally:
        adapter.close()

    assert context["verified"] is True
    assert context["element"]["text"] == "Ann"


def test_artifact_delivery_uses_clean_pre_paste_baseline_not_modal_preview(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    from PIL import Image

    from winpilot.tasking import adapters as adapter_module
    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    source_path = tmp_path / "artifact.png"
    frame_path = tmp_path / "conversation.png"
    Image.new("RGB", (120, 80), "white").save(source_path)
    Image.new("RGB", (1000, 800), "#202020").save(frame_path)
    store = ArtifactStore("run-clean-delivery-baseline", tmp_path / "managed")
    artifact = store.import_file(source_path)
    frame = store.import_file(
        frame_path,
        purpose="observation",
        sendable=False,
        metadata={"region": {"left": 0, "top": 0, "width": 1000, "height": 800}},
    )
    adapter = QQCommunicationAdapter(
        run_id="run-clean-delivery-baseline",
        task="send an artifact",
        store=store,
        evidence=EvidenceRegistry("run-clean-delivery-baseline"),
    )
    adapter.recipient = "recipient"
    adapter._send_action_at = time.time() - 1.0
    adapter._pre_paste_frame_id = "clean-baseline"
    adapter._preview_frame_id = "modal-contaminated-preview"
    snapshot = SimpleNamespace(region=Region(0, 0, 1000, 800), elements=[])
    adapter._observe_snapshot = lambda: snapshot
    adapter._recipient_header_context = lambda _snapshot: {"verified": True}
    adapter._capture_observation = lambda _purpose: frame
    baselines: list[str] = []

    def old_bubble_would_match_only_against_modal(*_args, **kwargs):
        baseline = str(kwargs.get("baseline_frame_id") or "")
        baselines.append(baseline)
        return {
            "matched": baseline == "modal-contaminated-preview",
            "match_bbox": [720, 300, 120, 80],
        }

    monkeypatch.setattr(store, "visual_match", old_bubble_would_match_only_against_modal)
    ticks = iter((0.0, 0.1, 5.1))
    monkeypatch.setattr(adapter_module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(adapter_module.time, "sleep", lambda _seconds: None)
    try:
        result = adapter.verify_latest_outbound(artifact_id=artifact.artifact_id)
    finally:
        adapter.close()

    assert result["ok"] is False
    assert baselines == ["clean-baseline"]


def test_text_delivery_rejects_unchanged_old_outbound_message(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    from PIL import Image, ImageDraw

    from winpilot.perception.model import UIElement
    from winpilot.tasking import adapters as adapter_module
    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    baseline_path = tmp_path / "baseline.png"
    baseline = Image.new("RGB", (1000, 800), "white")
    ImageDraw.Draw(baseline).rectangle((710, 290, 850, 340), fill="black")
    baseline.save(baseline_path)
    post_path = tmp_path / "post.png"
    baseline.save(post_path)
    store = ArtifactStore("run-old-text", tmp_path / "managed")
    metadata = {"region": {"left": 0, "top": 0, "width": 1000, "height": 800}}
    before = store.import_file(baseline_path, purpose="observation", sendable=False, metadata=metadata)
    post = store.import_file(post_path, purpose="observation", sendable=False, metadata=metadata)
    adapter = QQCommunicationAdapter(
        run_id="run-old-text", task="send text", store=store,
        evidence=EvidenceRegistry("run-old-text"),
    )
    adapter.recipient = "recipient"
    adapter._sent_text = "same text"
    adapter._pre_send_frame_id = before.artifact_id
    adapter._send_action_at = time.time() - 1.0
    candidate = UIElement(
        elem_id=1, source="ocr", role="text", text="same text",
        left=720, top=300, width=120, height=30, cx=780, cy=315,
    )
    snapshot = SimpleNamespace(region=Region(0, 0, 1000, 800), elements=[candidate])
    adapter._observe_snapshot = lambda: snapshot
    adapter._recipient_header_context = lambda _snapshot: {"verified": True}
    adapter._capture_observation = lambda _purpose: post
    adapter._message_text_matches = lambda _snapshot, _text: [candidate]
    ticks = iter((0.0, 0.1, 5.1))
    monkeypatch.setattr(adapter_module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(adapter_module.time, "sleep", lambda _seconds: None)
    try:
        result = adapter.verify_latest_outbound(text="same text")
    finally:
        adapter.close()

    assert result["ok"] is False
    assert result["candidate_evidence"][0]["outbound"] is True
    assert result["candidate_evidence"][0]["frame_change"]["changed"] is False


def test_text_delivery_accepts_new_changed_outbound_message(tmp_path):
    import time
    from types import SimpleNamespace

    from PIL import Image, ImageDraw

    from winpilot.perception.model import UIElement
    from winpilot.tasking.adapters import QQCommunicationAdapter
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.utils.screenshot import Region

    baseline_path = tmp_path / "baseline.png"
    Image.new("RGB", (1000, 800), "white").save(baseline_path)
    post_path = tmp_path / "post.png"
    post_image = Image.new("RGB", (1000, 800), "white")
    ImageDraw.Draw(post_image).rectangle((710, 290, 850, 340), fill="black")
    post_image.save(post_path)
    store = ArtifactStore("run-new-text", tmp_path / "managed")
    metadata = {"region": {"left": 0, "top": 0, "width": 1000, "height": 800}}
    before = store.import_file(baseline_path, purpose="observation", sendable=False, metadata=metadata)
    post = store.import_file(post_path, purpose="observation", sendable=False, metadata=metadata)
    adapter = QQCommunicationAdapter(
        run_id="run-new-text", task="send text", store=store,
        evidence=EvidenceRegistry("run-new-text"),
    )
    adapter.recipient = "recipient"
    adapter._sent_text = "same text"
    adapter._pre_send_frame_id = before.artifact_id
    adapter._send_action_at = time.time() - 1.0
    candidate = UIElement(
        elem_id=2, source="ocr", role="text", text="same text",
        left=720, top=300, width=120, height=30, cx=780, cy=315,
    )
    snapshot = SimpleNamespace(region=Region(0, 0, 1000, 800), elements=[candidate])
    adapter._observe_snapshot = lambda: snapshot
    adapter._recipient_header_context = lambda _snapshot: {"verified": True}
    adapter._capture_observation = lambda _purpose: post
    adapter._message_text_matches = lambda _snapshot, _text: [candidate]
    try:
        result = adapter.verify_latest_outbound(text="same text")
    finally:
        adapter.close()

    assert result["ok"] is True
    metadata = result["evidence"]["metadata"]
    assert metadata["baseline_frame_id"] == before.artifact_id
    assert metadata["candidate_evidence"][0]["outbound"] is True
    assert metadata["candidate_evidence"][0]["frame_change"]["changed"] is True
