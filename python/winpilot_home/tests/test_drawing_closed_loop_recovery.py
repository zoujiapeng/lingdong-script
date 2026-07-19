import json


def test_done_rejection_does_not_install_a_drawing_specific_mutation_guard(monkeypatch):
    from types import SimpleNamespace

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("create a polished illustration in the visible canvas")
    runner._visual_closed_loop_active = False
    runner._done_rejects = 2
    runner._last_done_reject_reason = "the latest frame did not yet satisfy the task"
    dispatched = []

    def dispatch(name, args):
        dispatched.append((name, args))
        return ToolResult('{"ok": true, "verified": false, "needs_observe": true}')

    monkeypatch.setattr(runner.session, "dispatch", dispatch)
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: None,
    )
    args = {
        "subgoal": "add a missing visible detail",
        "draws": [{
            "shape": "line",
            "part": "detail",
            "normalized_strokes": [[[100, 100], [180, 160], [230, 120]]],
        }],
    }
    tool_call = SimpleNamespace(
        id="continue-after-reject",
        function=SimpleNamespace(
            name="execute_subgoal",
            arguments=json.dumps(args),
        ),
    )

    assert runner._handle_tool_calls([tool_call]) is None
    assert dispatched == [("execute_subgoal", args)]
    payload = json.loads([
        message["content"]
        for message in runner.messages
        if message.get("role") == "tool"
    ][-1])
    assert payload["ok"] is True
    assert payload.get("blocked") is not True
    assert "drawing_quality_repair_limit_reached" not in payload


def test_model_draw_batch_is_forwarded_without_one_repair_compilation(monkeypatch):
    from types import SimpleNamespace

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("create an illustration in the visible canvas")
    runner._visual_closed_loop_active = False
    supplied = [
        {
            "shape": "line",
            "part": f"detail-{index}",
            "normalized_strokes": [[[index * 10, 10], [index * 10 + 5, 20]]],
        }
        for index in range(8)
    ]
    dispatched = []

    def dispatch(name, args):
        dispatched.append((name, args))
        return ToolResult('{"ok": true, "verified": false, "needs_observe": true}')

    monkeypatch.setattr(runner.session, "dispatch", dispatch)
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: None,
    )
    tool_call = SimpleNamespace(
        id="full-model-batch",
        function=SimpleNamespace(
            name="execute_subgoal",
            arguments=json.dumps({"subgoal": "apply visible repair", "draws": supplied}),
        ),
    )

    assert runner._handle_tool_calls([tool_call]) is None
    assert len(dispatched) == 1
    assert dispatched[0][0] == "execute_subgoal"
    assert dispatched[0][1]["draws"] == supplied


def test_tool_session_preserves_partial_subgoal_output_without_progress_classification(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="manipulate the visible fictional workspace")
    args = {
        "subgoal": "place one visible object",
        "draws": [{"shape": "ellipse", "part": "object", "box": [200, 250, 400, 250]}],
    }
    session._t_execute_subgoal = lambda _args: ToolResult(json.dumps({
        "ok": False,
        "verified": False,
        "evidence_packet": {"scene_structure": {"executed_draw_count": 1}},
        "actions_executed": [{"action": "draw_strokes", "part": "object"}],
        "partial_output": {"artifact_id": "fictional-7"},
    }))
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)

    payload = json.loads(session.dispatch("execute_subgoal", args).text)

    assert payload["ok"] is False
    assert payload["verified"] is False
    assert payload["partial_output"] == {"artifact_id": "fictional-7"}
    assert payload.get("blocked") is not True


def test_huge_drawing_result_compacts_outcome_and_actual_colors_before_geometry():
    from winpilot.agent.loop import AgentRunner

    parts = [
        "head", "body", "left_ear", "right_ear", "left_eye", "right_eye",
        "nose", "mouth", "left_front_leg", "right_front_leg", "left_back_leg",
        "right_back_leg", "tail", "left_paw", "right_paw", "collar", "tag",
        "left_brow", "right_brow", "fur_tuft",
    ]
    actions = []
    for index, part in enumerate(parts):
        actions.append({
            "action": "draw_strokes",
            "shape": "ellipse",
            "semantic_part": part,
            "requested_color": "brown" if index < 12 else "black",
            "canvas_region": {"x": 606, "y": 440, "width": 356, "height": 356},
            "draw_region": {"x": 700 + index, "y": 500, "width": 80, "height": 60},
            "normalized_box": [index * 10, 200, 180, 120],
            "strokes": [[
                [700 + point, 500 + ((point * 7) % 53)]
                for point in range(220)
            ]],
        })
    raw = json.dumps({
        "ok": True,
        "verified": True,
        "blocked": False,
        "mutation_applied": True,
        "drawing_scene_replacement_blocked": False,
        "structured_scene_verified": True,
        "subgoal": "Draw a detailed dog with brown body parts and black facial features",
        "actions_executed": actions,
        "results": [{"preview": "coordinate noise" * 500} for _ in actions],
        "evidence_packet": {
            "matched_visual_colors": ["black"],
            "missing_visual_colors": [],
            "optional_missing_visual_colors": ["brown"],
            "visible_colors": {
                "black": {"pixels": 1734, "ratio": 0.0137, "bbox": [1, 2, 300, 320]},
                "white": {"pixels": 99999, "ratio": 0.91},
            },
            "requested_visual_colors": ["brown", "black"],
            "required_visual_colors": [],
            "scene_structure": {
                "required": True,
                "ok": True,
                "creature_scene": True,
                "primitive_count": len(actions),
                "semantic_part_count": len(actions),
                "executed_draw_count": len(actions),
                "structured_scene_verified": True,
            },
        },
        "fast_path_advice": (
            "Brown was an optional planner style and was not applied. Black is the actual "
            "matched visible color. Preserve the delivered dog and do not redraw it."
        ),
    })

    compact_text = AgentRunner._compact_tool_evidence("execute_subgoal", raw)
    compact = json.loads(compact_text)

    assert len(compact_text) <= 1800
    assert compact["ok"] is True
    assert compact["verified"] is True
    assert compact["blocked"] is False
    assert compact["mutation_applied"] is True
    assert compact["drawing_scene_replacement_blocked"] is False
    assert compact["matched_visual_colors"] == ["black"]
    assert compact["missing_visual_colors"] == []
    assert compact["optional_missing_visual_colors"] == ["brown"]
    assert compact["effective_visual_colors"] == ["black"]
    assert compact["visible_colors"]["black"]["pixels"] == 1734
    assert "Brown was an optional planner style" in compact["fast_path_advice"]
    assert compact["draw_actions_summary"]["count"] == len(actions)
    assert compact["draw_actions_summary"]["parts"][:2] == ["head", "body"]
    assert compact["draw_actions_summary"]["canvas"] == {
        "x": 606, "y": 440, "width": 356, "height": 356,
    }
    assert "draw_region" not in compact_text
    assert "normalized_box" not in compact_text
    assert '"strokes"' not in compact_text


def test_blocked_bulk_undo_batch_compaction_keeps_destructive_outcome():
    from winpilot.agent.loop import AgentRunner

    raw = json.dumps({
        "ok": True,
        "verified": False,
        "blocked": True,
        "mutation_applied": False,
        "destructive_clear_blocked": True,
        "destructive_bulk_undo_blocked": True,
        "destructive_action": "bulk_undo",
        "needs_observe": False,
        "fast_path_advice": (
            "Visible drawing progress remains intact. Do not undo or clear again; "
            "call blocked/fail if the current canvas cannot pass the quality judge."
        ),
        "results": [
            {
                "action": "hotkey",
                "ok": False,
                "preview": "undo details " * 500,
                "data": {"verified": False, "debug": "x" * 4000},
            }
            for _ in range(40)
        ],
    })

    compact_text = AgentRunner._compact_tool_evidence("run_action_batch", raw)
    compact = json.loads(compact_text)

    assert len(compact_text) <= 1800
    assert compact["blocked"] is True
    assert compact["mutation_applied"] is False
    assert compact["destructive_action"] == "bulk_undo"
    assert compact["destructive_clear_blocked"] is True
    assert compact["destructive_bulk_undo_blocked"] is True
    assert "call blocked/fail" in compact["fast_path_advice"]


def test_folded_checkpoint_keeps_facts_and_drops_controller_tactics():
    from winpilot.agent.loop import AgentRunner

    compact = AgentRunner._compact_tool_evidence("execute_subgoal", json.dumps({
        "ok": True,
        "verified": True,
        "mutation_applied": True,
        "actions_executed": [{
            "action": "draw_strokes",
            "semantic_part": f"visible_part_{index}",
            "canvas_region": {"x": 10, "y": 20, "width": 800, "height": 500},
            "strokes": [[[point, point] for point in range(200)]],
        } for index in range(20)],
        "evidence_packet": {
            "matched_visual_colors": ["black"],
            "missing_visual_colors": [],
            "optional_missing_visual_colors": ["brown"],
        },
        "fast_path_advice": "Preserve the canvas; brown was not applied and black was matched.",
    }))
    runner = AgentRunner("modify the visible graphical workspace")
    runner._recent_results = [("execute_subgoal", compact)]

    checkpoint = runner._fold_checkpoint("old screenshot")

    assert len(checkpoint) <= 1800
    assert '"matched_visual_colors": ["black"]' in checkpoint
    assert '"optional_missing_visual_colors": ["brown"]' in checkpoint
    assert '"effective_visual_colors": ["black"]' in checkpoint
    assert "fast_path_advice" not in checkpoint
    assert "Preserve the canvas" not in checkpoint
    assert '"strokes":' not in checkpoint




def test_repeated_no_progress_subgoal_always_reaches_the_action_owner(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="operate the visible fictional workspace")
    session.world_enabled = False
    args = {
        "subgoal": "move the visible token",
        "draws": [{"part": "token", "shape": "ellipse", "box": [200, 200, 300, 200]}],
    }
    executed = []

    def no_progress_handler(actual_args):
        executed.append(actual_args)
        return ToolResult(json.dumps({
            "ok": False,
            "verified": False,
            "evidence_packet": {"scene_structure": {"executed_draw_count": 0}},
            "results": [{
                "action": "draw_strokes",
                "ok": False,
                "data": {"visual_change_detected": False},
            }],
        }))

    session._t_execute_subgoal = no_progress_handler
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)

    results = [session.dispatch("execute_subgoal", args) for _ in range(4)]

    assert all(result.terminal is None for result in results)
    assert all(json.loads(result.text)["verified"] is False for result in results)
    assert all(json.loads(result.text).get("blocked") is not True for result in results)
    assert len(executed) == 4

# Drawing completion and further mutation now share the generic fresh-frame loop.
