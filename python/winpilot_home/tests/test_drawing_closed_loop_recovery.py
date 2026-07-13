import json


_DOG_SCENE_PARTS = [
    "head",
    "body",
    "left_ear",
    "right_ear",
    "left_eye",
    "right_eye",
    "nose",
    "mouth",
    "left_front_leg",
    "right_front_leg",
    "tail",
]


def _dog_scene_draws(offset=0):
    return [
        {
            "shape": "ellipse" if part not in {"left_front_leg", "right_front_leg", "tail"} else "line",
            "part": part,
            "color": "black",
            "box": [100 + offset + index * 7, 120, 80, 60],
            "box_mode": "xywh",
        }
        for index, part in enumerate(_DOG_SCENE_PARTS)
    ]


def _verified_dog_scene_result():
    return json.dumps({
        "ok": True,
        "verified": True,
        "evidence_packet": {
            "matched_visual_colors": ["black"],
            "missing_visual_colors": [],
            "scene_structure": {
                "required": True,
                "primitive_count": len(_DOG_SCENE_PARTS),
                "semantic_part_count": len(_DOG_SCENE_PARTS),
                "distinct_semantic_parts": list(_DOG_SCENE_PARTS),
                "executed_draw_count": len(_DOG_SCENE_PARTS),
                "structured_scene_verified": True,
            },
        },
    })


def test_partial_unverified_drawing_blocks_unrequested_clear():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a good-looking dog in Paint")
    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw dog"},
        json.dumps({
            "ok": False,
            "verified": False,
            "evidence_packet": {
                "scene_structure": {"executed_draw_count": 2},
            },
            "results": [
                {"action": "draw_strokes", "ok": True, "data": {"deferred": True}},
                {"action": "select_color", "ok": False, "data": {"blocked": True}},
            ],
        }),
    ))

    result = runner._drawing_destructive_clear_result(
        "run_action_batch",
        {"actions": [
            {"action": "hotkey", "keys": ["ctrl", "a"]},
            {"action": "hotkey", "keys": ["delete"]},
        ]},
    )

    assert result is not None
    payload = json.loads(result.text)
    assert payload["destructive_clear_blocked"] is True
    assert payload["verified"] is False
    assert payload["mutation_applied"] is False


def test_partial_drawing_blocks_unrequested_new_canvas_restart():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw dog"},
        json.dumps({
            "ok": False,
            "verified": False,
            "evidence_packet": {"scene_structure": {"executed_draw_count": 3}},
        }),
    ))

    ctrl_n = runner._drawing_destructive_clear_result(
        "hotkey", {"keys": ["ctrl", "n"]},
    )
    new_click = runner._drawing_destructive_clear_result(
        "execute_subgoal", {"click": {"label": "New", "role": "button"}},
    )

    for result in (ctrl_n, new_click):
        assert result is not None
        payload = json.loads(result.text)
        assert payload["destructive_restart_blocked"] is True
        assert payload["verified"] is False


def test_partial_drawing_blocks_bulk_undo_restart_but_allows_one_repair_undo():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw dog"},
        json.dumps({
            "ok": True,
            "verified": True,
            "evidence_packet": {"scene_structure": {"executed_draw_count": 20}},
        }),
    ))

    one_undo = runner._drawing_destructive_clear_result(
        "hotkey", {"keys": ["ctrl", "z"]},
    )
    bulk_undo = runner._drawing_destructive_clear_result(
        "run_action_batch",
        {"actions": [
            {"action": "hotkey", "keys": ["ctrl", "z"]},
            {"action": "hotkey", "keys": ["ctrl", "z"]},
            {"action": "hotkey", "keys": ["ctrl", "z"]},
        ]},
    )

    assert one_undo is None
    assert bulk_undo is not None
    payload = json.loads(bulk_undo.text)
    assert payload["destructive_bulk_undo_blocked"] is True
    assert payload["mutation_applied"] is False


def test_verified_drawing_batch_is_not_reused_after_a_real_canvas_undo():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    args = {
        "subgoal": "draw dog",
        "draws": [
            {"shape": "ellipse", "part": "body", "box": [250, 450, 450, 260]},
            {"shape": "circle", "part": "head", "box": [350, 200, 250, 250]},
        ],
    }
    verified = json.dumps({
        "ok": True,
        "verified": True,
        "evidence_packet": {
            "matched_visual_colors": ["black"],
            "missing_visual_colors": [],
            "scene_structure": {"executed_draw_count": 2},
        },
    })
    runner._tool_history.extend([
        ("execute_subgoal", args, verified),
        ("hotkey", {"keys": ["ctrl", "z"]}, '{"ok": true}'),
    ])

    assert runner._maybe_reuse_recent_draw_batch("execute_subgoal", args) is None

    runner._tool_history[-1] = (
        "run_action_batch",
        {"actions": [
            {"action": "hotkey", "keys": ["ctrl", "z"]},
            {"action": "hotkey", "keys": ["ctrl", "z"]},
        ]},
        json.dumps({"ok": True, "blocked": True, "mutation_applied": False}),
    )
    reused = runner._maybe_reuse_recent_draw_batch("execute_subgoal", args)

    assert reused is not None
    assert json.loads(reused.text)["reused_recent_evidence"] is True


def test_single_draw_evidence_is_not_reused_after_a_real_canvas_undo():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    args = {
        "subgoal": "draw eye",
        "draw": {
            "shape": "circle",
            "part": "left_eye",
            "color": "black",
            "expected_region": "canvas",
        },
    }
    runner._tool_history.extend([
        (
            "execute_subgoal",
            args,
            json.dumps({
                "ok": True,
                "verified": True,
                "actions_executed": [{
                    "action": "draw_strokes",
                    "shape": "circle",
                    "requested_color": "black",
                }],
                "evidence_packet": {
                    "matched_visual_colors": ["black"],
                    "missing_visual_colors": [],
                },
            }),
        ),
        ("hotkey", {"keys": ["ctrl", "z"]}, '{"ok": true}'),
    ])

    assert runner._maybe_reuse_recent_draw_evidence(
        "execute_subgoal", args,
    ) is None


def test_verified_structured_scene_blocks_semantically_same_whole_dog_at_new_coordinates():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw the dog", "draws": _dog_scene_draws()},
        _verified_dog_scene_result(),
    ))

    replacement_draws = _dog_scene_draws(offset=240)
    for draw in replacement_draws:
        if draw["part"] == "body":
            draw["part"] = "torso"
        elif draw["part"] == "left_front_leg":
            draw["part"] = "front_left_leg"
        elif draw["part"] == "right_front_leg":
            draw["part"] = "front_right_leg"
    result = runner._drawing_scene_replacement_result(
        "execute_subgoal",
        {
            "subgoal": "draw a cuter complete dog",
            "draws": replacement_draws,
        },
    )

    assert result is not None
    payload = json.loads(result.text)
    assert payload["drawing_scene_replacement_blocked"] is True
    assert payload["verified"] is False
    assert payload["blocked"] is True
    assert payload["mutation_applied"] is False
    assert payload["semantic_overlap"]["overlap_count"] == len(_DOG_SCENE_PARTS)
    assert "brand-new missing decorative details" in payload["fast_path_advice"]


def test_scene_replacement_guard_blocks_refined_suffix_plus_extra_details():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw the dog", "draws": _dog_scene_draws()},
        _verified_dog_scene_result(),
    ))
    refined = [
        {**draw, "part": f"{draw['part']}_refined", "box": [500, 400, 90, 70]}
        for draw in _dog_scene_draws()
    ]
    refined.extend(
        {"shape": "line", "part": f"fur_texture_{index}", "box": [500, 500 + index, 20, 10]}
        for index in range(1, 9)
    )

    result = runner._drawing_scene_replacement_result(
        "execute_subgoal",
        {"subgoal": "refine the whole dog", "draws": refined},
    )

    assert result is not None
    payload = json.loads(result.text)
    assert payload["drawing_scene_replacement_blocked"] is True
    assert payload["semantic_overlap"]["overlap_count"] == len(_DOG_SCENE_PARTS)


def test_scene_replacement_guard_canonicalizes_chinese_part_synonyms():
    from winpilot.agent.loop import AgentRunner

    previous_parts = [
        "头部", "身体", "左耳", "右耳", "左眼", "右眼", "鼻子", "嘴巴",
        "左前腿", "右前腿", "左后腿", "右后腿", "左爪", "右爪", "尾巴",
        "项圈", "斑点",
    ]
    replacement_parts = [
        "脑袋", "躯干", "左耳朵", "右耳朵", "左眼睛", "右眼睛", "鼻", "嘴",
        "左前肢", "右前肢", "左后肢", "右后肢", "左脚掌", "右脚掌", "尾",
        "颈圈", "花纹",
    ]
    previous_args = {
        "subgoal": "draw the dog",
        "draws": [
            {"shape": "ellipse", "part": part, "box": [100 + index * 4, 120, 90, 70]}
            for index, part in enumerate(previous_parts)
        ],
    }
    verified = json.dumps({
        "ok": True,
        "verified": True,
        "structured_scene_verified": True,
        "evidence_packet": {
            "scene_structure": {
                "required": True,
                "primitive_count": len(previous_parts),
                "distinct_semantic_parts": previous_parts,
                "structured_scene_verified": True,
            },
        },
    }, ensure_ascii=False)
    runner = AgentRunner("draw a polished dog in Paint")
    runner._tool_history.append(("execute_subgoal", previous_args, verified))

    normalized = runner._drawing_semantic_parts_from_specs(previous_args["draws"])
    assert {"head", "body", "left_ear", "right_ear", "left_front_limb"} <= normalized

    result = runner._drawing_scene_replacement_result(
        "execute_subgoal",
        {
            "subgoal": "draw a cuter dog elsewhere",
            "draws": [
                {"shape": "ellipse", "part": part, "box": [500 + index * 4, 520, 90, 70]}
                for index, part in enumerate(replacement_parts)
            ],
        },
    )

    assert result is not None
    payload = json.loads(result.text)
    assert payload["drawing_scene_replacement_blocked"] is True
    assert payload["semantic_overlap"]["overlap_count"] >= 15


def test_verified_structured_scene_allows_new_details_and_single_part_repair():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw the dog", "draws": _dog_scene_draws()},
        _verified_dog_scene_result(),
    ))
    detail_draws = [
        {"shape": "line", "part": part, "box": [100, 100, 140, 140]}
        for part in (
            "left_whisker_1",
            "left_whisker_2",
            "right_whisker_1",
            "right_whisker_2",
            "collar",
            "collar_tag",
            "eye_highlight",
            "fur_tuft",
        )
    ]

    assert runner._drawing_scene_replacement_result(
        "execute_subgoal", {"draws": detail_draws},
    ) is None
    assert runner._drawing_scene_replacement_result(
        "execute_subgoal",
        {"draws": [{"shape": "ellipse", "part": "left_eye", "box": [300, 250, 20, 20]}]},
    ) is None


def test_scene_replacement_guard_does_not_use_evidence_after_real_canvas_change():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    runner._tool_history.extend([
        (
            "execute_subgoal",
            {"subgoal": "draw the dog", "draws": _dog_scene_draws()},
            _verified_dog_scene_result(),
        ),
        ("hotkey", {"keys": ["ctrl", "z"]}, '{"ok": true}'),
    ])

    result = runner._drawing_scene_replacement_result(
        "execute_subgoal",
        {"subgoal": "repair the dog", "draws": _dog_scene_draws(offset=200)},
    )

    assert result is None


def test_blocked_scene_replacement_does_not_invalidate_the_existing_scene_guard():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    original_args = {"subgoal": "draw the dog", "draws": _dog_scene_draws()}
    runner._tool_history.append((
        "execute_subgoal", original_args, _verified_dog_scene_result(),
    ))
    blocked_args = {"subgoal": "redraw elsewhere", "draws": _dog_scene_draws(offset=200)}
    first = runner._drawing_scene_replacement_result("execute_subgoal", blocked_args)
    assert first is not None
    runner._tool_history.append(("execute_subgoal", blocked_args, first.text))

    second = runner._drawing_scene_replacement_result(
        "execute_subgoal",
        {"subgoal": "redraw elsewhere again", "draws": _dog_scene_draws(offset=350)},
    )

    assert second is not None
    assert json.loads(second.text)["drawing_scene_replacement_blocked"] is True


def test_destructive_replan_is_blocked_only_after_a_verified_scene_exists():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    destructive_plan = {
        "steps": ["Clear the canvas completely", "Redraw the whole dog"],
    }
    assert runner._drawing_destructive_replan_result(
        "revise_plan", destructive_plan,
    ) is None

    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw the dog", "draws": _dog_scene_draws()},
        _verified_dog_scene_result(),
    ))
    result = runner._drawing_destructive_replan_result(
        "revise_plan", destructive_plan,
    )

    assert result is not None
    payload = json.loads(result.text)
    assert payload["destructive_replan_blocked"] is True
    assert payload["mutation_applied"] is False


def test_drawing_third_same_click_safely_pauses_across_frame_ids():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a dog in Paint")
    for frame_id in ("frame-1", "frame-2"):
        runner._tool_history.append((
            "click",
            {
                "x": 700,
                "y": 80,
                "visual_frame_id": frame_id,
                "source_frame_coordinates": {"frame_x": 820, "frame_y": 120},
            },
            '{"ok": true}',
        ))

    result = runner._drawing_repeated_click_result(
        "click",
        {
            "x": 702,
            "y": 80,
            "visual_frame_id": "frame-3",
            "source_frame_coordinates": {"frame_x": 821, "frame_y": 119},
        },
    )

    assert result is not None
    assert result.terminal == "reply"
    payload = json.loads(result.text)
    assert payload["duplicate_no_progress_action"] is True
    assert payload["repeat_count"] == 3


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
    from winpilot.agent.loop import AgentRunner
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
    assert AgentRunner._drawing_payload_has_progress(payload) is False


def test_chinese_quality_request_activates_detailed_drawing_gate():
    from winpilot.agent.done_verifier import _DETAILED_DRAWING_RE

    task = "\u7528\u753b\u677f\u753b\u4e00\u53ea\u72d7,\u8981\u597d\u770b\u4e00\u70b9\u4e0d\u8981\u7b80\u964b\u7684"

    assert _DETAILED_DRAWING_RE.search(task)


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


def test_second_drawing_quality_reject_blocks_more_canvas_mutation(monkeypatch):
    from types import SimpleNamespace

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("draw a polished dog in Paint")
    runner._visual_closed_loop_active = False
    runner._drawing_quality_rejects = 2
    runner._last_done_reject_reason = "the dog is still crude and incomplete"
    dispatched = []

    def dispatch(name, args):
        dispatched.append((name, args))
        return ToolResult('{"ok": true}')

    monkeypatch.setattr(runner.session, "dispatch", dispatch)
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: None,
    )
    tool_call = SimpleNamespace(
        id="repair-too-far",
        function=SimpleNamespace(
            name="execute_subgoal",
            arguments=json.dumps({
                "subgoal": "redraw the dog again",
                "draws": [{"shape": "ellipse", "part": "body", "box": [1, 2, 3, 4]}],
            }),
        ),
    )

    terminal = runner._handle_tool_calls([tool_call])
    tool_payload = json.loads([
        message["content"]
        for message in runner.messages
        if message.get("role") == "tool"
    ][-1])

    assert terminal is None
    assert dispatched == []
    assert tool_payload["blocked"] is True
    assert tool_payload["mutation_applied"] is False
    assert tool_payload["drawing_quality_repair_limit_reached"] is True
    assert tool_payload["drawing_quality_rejects"] == 2
    assert "Call blocked or fail" in tool_payload["fast_path_advice"]
    for allowed_tool in ("done", "blocked", "fail", "observe"):
        assert runner._drawing_quality_repair_limit_result(allowed_tool, {}) is None
    assert runner._drawing_quality_repair_limit_result(
        "run_action_batch",
        {"actions": [{"action": "wait_stable", "timeout_s": 1.0}]},
    ) is None


def test_first_quality_reject_allows_one_small_new_detail_batch_only():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    original_args = {"subgoal": "draw the dog", "draws": _dog_scene_draws()}
    runner._tool_history.append((
        "execute_subgoal", original_args, _verified_dog_scene_result(),
    ))
    runner._drawing_quality_rejects = 1
    runner._last_done_reject_reason = "the dog needs more facial detail"
    runner._drawing_quality_repair_history_start = len(runner._tool_history)

    repeated_core = {
        "draws": [
            {"shape": "ellipse", "part": part, "box": [500, 500, 90, 70]}
            for part in ("head", "body", "left_ear", "right_ear", "left_eye")
        ],
    }
    core_result = runner._drawing_quality_repair_limit_result(
        "execute_subgoal", repeated_core,
    )
    assert core_result is not None
    core_payload = json.loads(core_result.text)
    assert core_payload["drawing_quality_repair_blocked"] is True
    assert set(core_payload["rejected_semantic_parts"]) == {
        "head", "body", "left_ear", "right_ear", "left_eye",
    }

    detail_args = {
        "draws": [
            {"shape": "line", "part": "whisker_top_left", "box": [300, 290, 340, 295]},
            {"shape": "line", "part": "whisker_bottom_left", "box": [300, 310, 340, 305]},
            {"shape": "line", "part": "whisker_top_right", "box": [600, 290, 640, 295]},
            {"shape": "line", "part": "whisker_bottom_right", "box": [600, 310, 640, 305]},
            {"shape": "circle", "part": "left_eye_highlight", "box": [420, 220, 18, 18]},
        ],
    }
    # A non-mutating observation after the judge does not consume the repair.
    runner._tool_history.append((
        "observe", {}, json.dumps({"ok": True, "visual_change_detected": True}),
    ))
    assert runner._drawing_quality_repair_limit_result(
        "execute_subgoal", detail_args,
    ) is None

    runner._tool_history.append((
        "execute_subgoal",
        detail_args,
        json.dumps({
            "ok": True,
            "verified": True,
            "evidence_packet": {"scene_structure": {"executed_draw_count": 5}},
        }),
    ))
    second_result = runner._drawing_quality_repair_limit_result(
        "execute_subgoal",
        {"draw": {"shape": "ellipse", "part": "right_cheek", "box": [600, 300, 30, 20]}},
    )
    assert second_result is not None
    second_payload = json.loads(second_result.text)
    assert second_payload["drawing_quality_repair_blocked"] is True
    assert second_payload["drawing_quality_repair_used"] is True


def test_oversized_quality_repair_compiles_supplied_safe_details_once():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw the dog", "draws": _dog_scene_draws()},
        _verified_dog_scene_result(),
    ))
    runner._drawing_quality_rejects = 1
    runner._drawing_quality_repair_history_start = len(runner._tool_history)
    supplied = {
        "subgoal": "add facial detail",
        "draws": [
            {"shape": "circle", "part": "eye_pupil_left", "box": [230, 350, 10, 10]},
            {"shape": "circle", "part": "eye_pupil_right", "box": [310, 350, 10, 10]},
            {"shape": "line", "part": "mouth_smile", "box": [260, 460, 40, 10]},
            {"shape": "line", "part": "whisker_top_left", "box": [220, 430, 40, -10]},
            {"shape": "line", "part": "whisker_bottom_left", "box": [220, 440, 40, 10]},
            {"shape": "line", "part": "whisker_top_right", "box": [340, 430, 40, -10]},
            {"shape": "line", "part": "whisker_bottom_right", "box": [340, 440, 40, 10]},
            {"shape": "line", "part": "eyebrow_left", "box": [225, 330, 40, -5]},
            {"shape": "line", "part": "eyebrow_right", "box": [305, 325, 40, 5]},
            {"shape": "circle", "part": "collar_detail", "box": [295, 495, 10, 10]},
        ],
    }

    compiled = runner._compile_drawing_quality_repair_args(
        "execute_subgoal", supplied,
    )

    assert len(compiled["draws"]) == 5
    assert [item["part"] for item in compiled["draws"]] == [
        "eye_pupil_left",
        "eye_pupil_right",
        "whisker_top_left",
        "whisker_bottom_left",
        "whisker_top_right",
    ]
    assert runner._drawing_quality_repair_limit_result(
        "execute_subgoal", compiled,
    ) is None
    assert supplied["draws"][2]["part"] == "mouth_smile"


def test_unknown_numbered_drawing_part_keeps_its_label():
    from winpilot.agent.loop import AgentRunner

    assert AgentRunner._drawing_normalized_semantic_part("star_1") == "star_1"
    assert AgentRunner._drawing_normalized_semantic_part("bone_2") == "bone_2"


def test_drawing_quality_blocked_result_cannot_claim_success():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    runner._drawing_quality_rejects = 1
    runner._last_done_reject_reason = "the dog is still crude geometry"

    compiled = runner._compile_drawing_quality_terminal_args("blocked", {
        "reason": "the current image meets the basic request",
        "evidence": "finished",
        "question": "accept it?",
    })
    result = runner.session.dispatch("blocked", compiled)

    assert result.terminal == "reply"
    assert "未标记为成功" in result.text
    assert "the dog is still crude geometry" in result.text
    assert "meets the basic request" not in result.text

    runner._drawing_quality_rejects = 2
    exhausted = runner._drawing_quality_exhausted_reply()
    assert "连续两次未通过" in exhausted
    assert "没有把这次结果标记为成功" in exhausted
    assert "the dog is still crude geometry" in exhausted


def test_verified_structured_drawing_auto_judges_without_another_model_turn():
    from winpilot.agent.loop import AgentRunner

    payload = {
        "ok": True,
        "verified": True,
        "structured_scene_verified": True,
    }
    runner = AgentRunner("draw a polished dog in Paint")
    assert runner._drawing_structured_scene_should_auto_judge(
        "execute_subgoal", payload,
    ) is True
    assert runner._drawing_structured_scene_should_auto_judge(
        "click", payload,
    ) is False

    saved = AgentRunner("draw a polished dog and save it as dog.png")
    assert saved._drawing_structured_scene_should_auto_judge(
        "execute_subgoal", payload,
    ) is False


def test_repeated_no_progress_drawing_subgoal_terminates_honestly():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="draw a polished dog in Paint")
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

    assert result.terminal == "reply"
    assert "连续两次没有产生可验证" in result.text
    assert "没有把本次任务标记为成功" in result.text
    assert executed == []


def test_no_progress_drawing_subgoal_retries_once_after_canvas_recovery(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="draw a polished dog in Paint")
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
    assert static_retry.terminal == "reply"
    assert len(executed) == 3


def test_verified_scene_blocks_low_level_mutations_even_for_color_tasks():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw the dog", "draws": _dog_scene_draws()},
        _verified_dog_scene_result(),
    ))

    blocked = runner._drawing_post_scene_manual_mutation_result(
        "click", {"x": 922, "y": 298},
    )
    assert blocked is not None
    assert blocked.terminal == "done"
    assert "直接提交独立视觉验收" in blocked.text

    colored = AgentRunner("draw a polished blue dog in Paint")
    colored._tool_history = list(runner._tool_history)
    color_block = colored._drawing_post_scene_manual_mutation_result(
        "click", {"x": 922, "y": 298},
    )
    assert color_block is not None
    assert color_block.terminal == "done"

    negative_color = AgentRunner("draw a polished dog, do not use red")
    negative_color._tool_history = list(runner._tool_history)
    assert negative_color._drawing_post_scene_manual_mutation_result(
        "select_color", {"color": "red"},
    ) is not None

    assert runner._drawing_post_scene_manual_mutation_result(
        "draw_strokes", {"strokes": [[[1, 1], [2, 2]]]},
    ) is not None


def test_post_scene_guard_handles_batch_shorthand_and_auto_judges():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog in Paint")
    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw the dog", "draws": _dog_scene_draws()},
        _verified_dog_scene_result(),
    ))
    batch_args = {"actions": [{"click": {"x": 922, "y": 298}}]}

    first = runner._drawing_post_scene_manual_mutation_result(
        "run_action_batch", batch_args,
    )

    assert first is not None
    assert first.terminal == "done"
    assert "直接提交独立视觉验收" in first.text


def test_post_scene_guard_only_allows_explicit_file_output_controls():
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a polished dog and save it as dog.png")
    runner._tool_history.append((
        "execute_subgoal",
        {"subgoal": "draw the dog", "draws": _dog_scene_draws()},
        _verified_dog_scene_result(),
    ))

    assert runner._drawing_post_scene_manual_mutation_result(
        "hotkey", {"keys": ["ctrl", "s"]},
    ) is None
    assert runner._drawing_post_scene_manual_mutation_result(
        "click_element", {"label": "Save As"},
    ) is None
    assert runner._drawing_post_scene_manual_mutation_result(
        "click", {"x": 922, "y": 298},
    ) is not None
    assert runner._drawing_post_scene_manual_mutation_result(
        "run_action_batch",
        {"actions": [{"hotkey": {"keys": ["ctrl", "s"]}}]},
    ) is None
