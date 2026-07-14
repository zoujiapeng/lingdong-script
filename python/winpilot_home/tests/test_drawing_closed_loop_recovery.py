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


def test_delivered_draw_progress_resets_subgoal_failure_debt(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="draw a polished dog in Paint")
    args = {
        "subgoal": "draw dog",
        "draws": [{"shape": "ellipse", "part": "body", "box": [200, 250, 400, 250]}],
    }
    signature = session._subgoal_signature(args)
    session._subgoal_no_progress_counts[signature] = 1
    session._t_execute_subgoal = lambda _args: ToolResult(json.dumps({
        "ok": False,
        "verified": False,
        "evidence_packet": {"scene_structure": {"executed_draw_count": 1}},
        "actions_executed": [{"action": "draw_strokes", "part": "body"}],
    }))
    monkeypatch.setattr(scope_guard, "ensure_scope", lambda *_args, **_kwargs: None)

    session.dispatch("execute_subgoal", args)

    assert signature not in session._subgoal_no_progress_counts


def test_failed_draw_action_metadata_does_not_fake_visual_progress():
    from winpilot.agent.tools import ToolSession

    payload = {
        "ok": False,
        "verified": False,
        "actions_executed": [{"action": "draw_strokes", "semantic_part": "body"}],
        "results": [{
            "action": "draw_strokes",
            "ok": False,
            "data": {
                "visual_change_detected": False,
                "failure_type": "DRAW_NO_VISIBLE_CHANGE",
            },
        }],
        "evidence_packet": {
            "scene_structure": {"executed_draw_count": 0},
            "per_draw_color_evidence": [],
        },
    }

    assert ToolSession._subgoal_has_visual_progress(payload) is False


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


def test_folded_checkpoint_keeps_latest_drawing_outcome_first():
    from winpilot.agent.loop import AgentRunner

    compact = AgentRunner._compact_tool_evidence("execute_subgoal", json.dumps({
        "ok": True,
        "verified": True,
        "mutation_applied": True,
        "actions_executed": [{
            "action": "draw_strokes",
            "semantic_part": f"detail_{index}",
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
    runner = AgentRunner("draw a polished dog in Paint")
    runner._recent_results = [("execute_subgoal", compact)]

    checkpoint = runner._fold_checkpoint("old screenshot")

    assert len(checkpoint) <= 1800
    assert '"matched_visual_colors": ["black"]' in checkpoint
    assert '"optional_missing_visual_colors": ["brown"]' in checkpoint
    assert '"effective_visual_colors": ["black"]' in checkpoint
    assert "fast_path_advice" in checkpoint
    assert "Preserve the canvas" in checkpoint
    assert '"strokes":' not in checkpoint




def test_repeated_no_progress_subgoal_blocks_without_forcing_terminal_reply():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="create an illustration in the visible canvas")
    args = {
        "subgoal": "draw dog",
        "draws": [{"part": "body", "shape": "ellipse", "box": [200, 200, 300, 200]}],
    }
    signature = session._subgoal_signature(args)
    session._subgoal_no_progress_counts[signature] = 2
    session._subgoal_no_progress_contexts[signature] = (
        session._subgoal_recovery_context()
    )
    executed = []
    session._t_execute_subgoal = lambda _args: executed.append(_args)

    result = session.dispatch("execute_subgoal", args)

    payload = json.loads(result.text)
    assert result.terminal is None
    assert payload["blocked"] is True
    assert payload["duplicate_no_progress_subgoal"] is True
    assert payload["verified"] is False
    assert executed == []


def test_no_progress_subgoal_retries_once_after_real_context_recovery(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="create an illustration in the visible canvas")
    session.world_enabled = False
    args = {
        "subgoal": "draw dog",
        "draws": [{"part": "body", "shape": "ellipse", "box": [200, 200, 300, 200]}],
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

    session.dispatch("execute_subgoal", args)
    session.dispatch("execute_subgoal", args)
    signature = session._subgoal_signature(args)
    assert session._subgoal_no_progress_counts[signature] == 2

    # The controller corrected both its target and the canvas transform/generation.
    session.hwnd = 4321
    session._canvas_generation = 1
    session._last_canvas_region = {
        "x": 80,
        "y": 140,
        "width": 900,
        "height": 620,
    }
    recovered_retry = session.dispatch("execute_subgoal", args)

    assert recovered_retry.terminal is None
    assert len(executed) == 3
    assert session._subgoal_no_progress_counts[signature] == 3

    # The recovery transition is consumed: the unchanged corrected canvas
    # cannot be used to resend the same coordinates indefinitely.
    static_retry = session.dispatch("execute_subgoal", args)
    static_payload = json.loads(static_retry.text)
    assert static_retry.terminal is None
    assert static_payload["blocked"] is True
    assert static_payload["duplicate_no_progress_subgoal"] is True
    assert len(executed) == 3

# Drawing completion and further mutation now share the generic fresh-frame loop.
