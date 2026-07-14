from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from winpilot.agent.semantic_progress import (
    ContinuousSemanticProgress,
    TargetKey,
    compare_geometry,
    fingerprint_geometry,
    structured_effect_applied,
    structured_primitive_paths,
)


TARGET = TargetKey("surface-a", (0.0, 0.0, 1000.0, 1000.0))


def _tool_call(call_id: str, name: str, args: dict):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _first_paths():
    outer = [[100, 500], [300, 200], [700, 200], [900, 500],
             [700, 800], [300, 800], [100, 500]]
    upper = [[250, 500], [500, 200], [750, 500]]
    lower = [[250, 500], [500, 800], [750, 500]]
    return [outer, upper, lower, upper, lower, upper, lower]


def _paired_paths(x_offset: int = 0):
    upper = [[350 + x_offset, 500], [650 + x_offset, 200], [950 + x_offset, 500]]
    lower = [[350 + x_offset, 500], [650 + x_offset, 800], [950 + x_offset, 500]]
    return [upper, lower, upper, lower, upper, lower]


def _novel_paths():
    return [
        [[80, 250], [180, 110], [330, 170], [410, 330]],
        [[260, 520], [390, 420], [560, 510], [710, 390]],
        [[440, 720], [520, 650], [610, 735], [760, 620]],
    ]


def _geometry(paths):
    return fingerprint_geometry(paths, surface=(0.0, 0.0, 1000.0, 1000.0))


def test_structured_shape_box_expands_to_stable_canvas_geometry():
    circle = {"shape": "circle", "box": [120, 180, 260, 260]}
    same = {"shape": "circle", "box": [120.0, 180.0, 260.0, 260.0]}
    moved = {"shape": "circle", "box": [520, 180, 260, 260]}
    line = {"shape": "line", "box": [100, 200, 300, 400]}
    offset_line = {
        "shape": "line",
        "box": [100, 200, 300, 400],
        "box_mode": "xywh",
    }
    point_triangle = {
        "shape": "triangle",
        "box": [0, 120, 500, 0, 1000, 120],
    }

    first = _geometry(structured_primitive_paths(circle))
    repeated = _geometry(structured_primitive_paths(same))
    translated = _geometry(structured_primitive_paths(moved))

    assert first.signature == repeated.signature
    assert first.signature != translated.signature
    assert structured_primitive_paths(line) == [[[100.0, 200.0], [300.0, 400.0]]]
    assert structured_primitive_paths(offset_line) == [[[100.0, 200.0], [400.0, 600.0]]]
    assert structured_primitive_paths(point_triangle) == [[
        [30.0, 120.0],
        [500.0, 30.0],
        [970.0, 120.0],
        [30.0, 120.0],
    ]]
    triangle_geometry = _geometry(structured_primitive_paths(point_triangle))
    repeated_triangle = _geometry(structured_primitive_paths({
        "shape": "triangle",
        "box": [0.0, 120.0, 500.0, 0.0, 1000.0, 120.0],
    }))
    assert triangle_geometry.signature == repeated_triangle.signature
    assert structured_primitive_paths({"shape": "square", "box": [0, 0, 10, 10]})
    assert structured_primitive_paths({"shape": "unknown", "box": [0, 0, 10, 10]}) == []


def _stalled_state() -> tuple[ContinuousSemanticProgress, object]:
    state = ContinuousSemanticProgress()
    first = _geometry(_first_paths())
    second = _geometry(_paired_paths())

    assert state.record_effect(target=TARGET, geometry=first, applied=True)
    assert state.record_description(
        "The visible result is a rough three dimensional rectangular frame "
        "with thick uneven dark edges and overlapping corners.",
        target=TARGET,
    )
    assert state.record_effect(target=TARGET, geometry=second, applied=True)
    assert state.record_description(
        "The visible result remains a rough three dimensional rectangular frame "
        "with thick uneven dark edges and overlapping corners.",
        target=TARGET,
    )
    assert state.snapshot.semantic_stagnation is True
    return state, second


def test_pixel_effect_is_not_semantic_progress():
    dispatch_only = {
        "ok": True,
        "verified": True,
        "semantic_verified": False,
        "summary_kind": "pixel_effect_only",
    }
    with_effect = {
        **dispatch_only,
        "auto_visual_verify": {
            "ok": True,
            "effect_observed": True,
            "semantic_verified": False,
        },
    }

    assert structured_effect_applied(dispatch_only) is False
    assert structured_effect_applied(with_effect) is True

    state = ContinuousSemanticProgress()
    assert state.record_effect(
        target=TARGET,
        geometry=_geometry(_first_paths()),
        applied=structured_effect_applied(with_effect),
    )
    snapshot = state.snapshot
    assert snapshot.effect_generation == 1
    assert snapshot.observation_generation == 0
    assert snapshot.pixel_effect_only is True
    assert snapshot.semantic_stagnation is False


def test_repeated_path_ratios_match_observed_failure_shape():
    seven_with_three_unique = _geometry(_first_paths())
    six_with_two_unique = _geometry(_paired_paths())

    assert seven_with_three_unique.path_count == 7
    assert seven_with_three_unique.unique_path_count == 3
    assert seven_with_three_unique.duplicate_ratio == pytest.approx(4 / 7)
    assert six_with_two_unique.path_count == 6
    assert six_with_two_unique.unique_path_count == 2
    assert six_with_two_unique.duplicate_ratio == pytest.approx(4 / 6)

    translated = _geometry(_paired_paths(-50))
    comparison = compare_geometry(six_with_two_unique, translated)
    assert comparison.placement_iou == pytest.approx(0.8461538)
    assert comparison.shape_similarity == 1.0
    assert comparison.near_repeat is True


def test_similar_observations_and_near_repeat_block_then_safe_stop():
    state, _second = _stalled_state()
    candidate = _geometry(_paired_paths(-50))

    first = state.evaluate_candidate(target=TARGET, geometry=candidate)
    assert first.allowed is False
    assert first.reason == "material_change_required"
    assert first.safe_stop is False
    assert first.guard_blocks == 1
    assert first.comparison is not None and first.comparison.near_repeat is True

    repeated = state.evaluate_candidate(target=TARGET, geometry=candidate)
    assert repeated.allowed is False
    assert repeated.reason == "material_change_required"
    assert repeated.safe_stop is True
    assert repeated.guard_blocks == 2


def test_materially_novel_geometry_is_allowed_then_needs_fresh_semantic():
    state, _second = _stalled_state()
    novel = _geometry(_novel_paths())

    repair = state.evaluate_candidate(target=TARGET, geometry=novel)
    assert repair.allowed is True
    assert repair.reason == "material_change_allowed"
    assert repair.requires_fresh_semantic is True
    assert repair.comparison is not None and repair.comparison.near_repeat is False

    assert state.record_effect(target=TARGET, geometry=novel, applied=True)
    assert state.snapshot.fresh_semantic_required is True

    before_fresh_observation = state.evaluate_candidate(
        target=TARGET,
        geometry=_geometry(_novel_paths()),
    )
    assert before_fresh_observation.allowed is False
    assert before_fresh_observation.reason == "fresh_semantic_required"
    assert before_fresh_observation.safe_stop is False

    assert state.record_description(
        "The visible result now contains several smooth curved regions and "
        "distinct small details distributed across the surface.",
        target=TARGET,
    )
    assert state.snapshot.fresh_semantic_required is False
    assert state.snapshot.semantic_stagnation is False

    after_fresh_observation = state.evaluate_candidate(
        target=TARGET,
        geometry=_geometry([
            [[120, 150], [250, 340], [440, 210]],
            [[540, 610], [700, 430], [860, 650]],
        ]),
    )
    assert after_fresh_observation.allowed is True


def test_target_change_resets_stagnation_and_blocks():
    state, _second = _stalled_state()
    candidate = _geometry(_paired_paths(-50))
    assert state.evaluate_candidate(target=TARGET, geometry=candidate).allowed is False

    other = TargetKey("surface-b", (1200.0, 0.0, 1000.0, 1000.0))
    decision = state.evaluate_candidate(target=other, geometry=candidate)
    assert decision.allowed is True
    assert decision.reason == "target_changed"
    assert state.snapshot.target == other
    assert state.snapshot.effect_generation == 0
    assert state.snapshot.semantic_stagnation is False
    assert state.snapshot.guard_blocks == 0


def test_failed_or_low_confidence_semantic_calls_do_not_count():
    state = ContinuousSemanticProgress()
    first = _geometry(_first_paths())
    second = _geometry(_paired_paths())
    assert state.record_effect(target=TARGET, geometry=first, applied=True)

    assert state.record_description("service unavailable", target=TARGET, ok=False) is False
    assert state.record_verification(
        target=TARGET,
        ok=False,
        passed=False,
        confidence=0.99,
        details={"issues": ["unchanged state"]},
    ) is False
    assert state.record_verification(
        target=TARGET,
        ok=True,
        passed=False,
        confidence=0.40,
        minimum_confidence=0.80,
        details={"issues": ["unchanged state"]},
    ) is False
    assert state.snapshot.observation_generation == 0

    assert state.record_description(
        "A stable angular structure occupies the center of the surface.",
        target=TARGET,
    )
    assert state.record_effect(target=TARGET, geometry=second, applied=True)
    assert state.record_description("timeout", target=TARGET, ok=False) is False
    assert state.snapshot.observation_generation == 1
    assert state.snapshot.pixel_effect_only is True
    assert state.snapshot.semantic_stagnation is False


def test_similar_high_confidence_failures_can_mark_stagnation():
    state = ContinuousSemanticProgress()
    assert state.record_effect(
        target=TARGET,
        geometry=_geometry(_first_paths()),
        applied=True,
    )
    assert state.record_verification(
        target=TARGET,
        ok=True,
        passed=False,
        confidence=0.93,
        details={
            "issues": ["requested structure is absent", "quality remains rough"],
            "detail_level": "low",
            "subject_present": False,
        },
    )
    assert state.record_effect(
        target=TARGET,
        geometry=_geometry(_paired_paths()),
        applied=True,
    )
    assert state.record_verification(
        target=TARGET,
        ok=True,
        passed=False,
        confidence=0.95,
        details={
            "issues": ["requested structure is still absent", "quality remains rough"],
            "detail_level": "low",
            "subject_present": False,
        },
    )
    assert state.snapshot.semantic_stagnation is True


def test_handler_blocks_without_dispatch_then_returns_safe_stop(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: False)
    runner = AgentRunner("trace the visible outline on the current work surface")
    runner.session.hwnd = 77
    runner.messages = [{"role": "system", "content": "sys"}]
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: None,
    )
    dispatched: list[str] = []
    descriptions = iter([
        "The visible result is a rough three dimensional rectangular frame "
        "with thick uneven dark edges and overlapping corners.",
        "The visible result remains a rough three dimensional rectangular frame "
        "with thick uneven dark edges and overlapping corners.",
    ])

    def dispatch(name, _args):
        dispatched.append(name)
        if name == "vlm_describe":
            return ToolResult(next(descriptions), is_observe=True)
        return ToolResult(json.dumps({
            "ok": True,
            "verified": True,
            "visual_change_detected": True,
            "semantic_verified": False,
            "summary_kind": "pixel_effect_only",
        }))

    monkeypatch.setattr(runner, "_dispatch_with_fast_visual_wrap", dispatch)

    assert runner._handle_tool_calls([_tool_call(
        "effect-1", "draw_strokes", {"normalized_strokes": _first_paths()},
    )]) is None
    assert runner._handle_tool_calls([_tool_call(
        "observe-1", "vlm_describe", {"question": "Describe the current visible result."},
    )]) is None
    assert runner._handle_tool_calls([_tool_call(
        "effect-2", "run_action_batch", {
            "actions": [{
                "action": "draw_strokes",
                "normalized_strokes": _paired_paths(),
            }],
        },
    )]) is None
    assert runner._handle_tool_calls([_tool_call(
        "observe-2", "vlm_describe", {"question": "Describe the current visible result."},
    )]) is None
    assert dispatched == [
        "draw_strokes", "vlm_describe", "run_action_batch", "vlm_describe",
    ]

    first_block = runner._handle_tool_calls([_tool_call(
        "blocked-1", "run_action_batch", {
            "actions": [{
                "action": "draw_strokes",
                "normalized_strokes": _paired_paths(-50),
            }],
        },
    )])
    assert first_block is None
    assert dispatched == [
        "draw_strokes", "vlm_describe", "run_action_batch", "vlm_describe",
    ]
    first_payload = json.loads(runner._tool_history[-1][2])
    assert first_payload["semantic_progress_guard"] is True
    assert first_payload["blocked"] is True
    assert first_payload["safe_stop"] is False

    terminal = runner._handle_tool_calls([_tool_call(
        "blocked-2", "draw_strokes", {
            "normalized_strokes": _paired_paths(-50),
        },
    )])
    assert terminal == "reply"
    assert dispatched == [
        "draw_strokes", "vlm_describe", "run_action_batch", "vlm_describe",
    ]
    stopped_payload = json.loads(runner._tool_history[-1][2])
    assert stopped_payload["semantic_progress_guard"] is True
    assert stopped_payload["safe_stop"] is True
    assert stopped_payload["guard_blocks"] == 2


@pytest.mark.parametrize(
    "draw_spec",
    [
        {"shape": "circle", "box": [180, 180, 360, 360]},
        {"shape": "triangle", "box": [160, 620, 500, 140, 840, 620]},
    ],
    ids=["shape-box", "multi-point-triangle"],
)
def test_handler_guards_repeated_execute_subgoal_structured_primitive(
    monkeypatch, draw_spec,
):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: False)
    runner = AgentRunner("draw a clean circle on the current work surface")
    runner.session.hwnd = 79
    runner.messages = [{"role": "system", "content": "sys"}]
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: None,
    )
    dispatched: list[str] = []
    descriptions = iter([
        "The current result is a rough incomplete round outline with uneven edges.",
        "The current result remains a rough incomplete round outline with uneven edges.",
    ])

    def dispatch(name, _args):
        dispatched.append(name)
        if name == "vlm_describe":
            return ToolResult(next(descriptions), is_observe=True)
        return ToolResult(json.dumps({
            "ok": True,
            "verified": False,
            "visual_change_detected": True,
            "semantic_verified": False,
        }))

    monkeypatch.setattr(runner, "_dispatch_with_fast_visual_wrap", dispatch)
    primitive = {"draw": draw_spec}

    assert runner._continuous_semantic_geometry("execute_subgoal", primitive) is not None
    assert runner._handle_tool_calls([
        _tool_call("shape-1", "execute_subgoal", primitive),
    ]) is None
    assert runner._continuous_semantic_progress.snapshot.effect_generation == 1
    assert runner._handle_tool_calls([
        _tool_call("describe-1", "vlm_describe", {"question": "Describe the visible result."}),
    ]) is None
    assert runner._handle_tool_calls([
        _tool_call("shape-2", "execute_subgoal", primitive),
    ]) is None
    assert runner._continuous_semantic_progress.snapshot.effect_generation == 2
    assert runner._handle_tool_calls([
        _tool_call("describe-2", "vlm_describe", {"question": "Describe the visible result."}),
    ]) is None
    assert runner._continuous_semantic_progress.snapshot.semantic_stagnation is True

    before = list(dispatched)
    assert runner._handle_tool_calls([
        _tool_call("shape-blocked", "execute_subgoal", primitive),
    ]) is None
    assert dispatched == before
    blocked = json.loads(runner._tool_history[-1][2])
    assert blocked["semantic_progress_guard"] is True
    assert blocked["blocked"] is True
    assert blocked["safe_stop"] is False


def test_handler_records_fresh_structured_verification(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: False)
    runner = AgentRunner("trace the visible outline on the current work surface")
    runner.session.hwnd = 88
    runner.messages = [{"role": "system", "content": "sys"}]
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: None,
    )

    def dispatch(name, _args):
        if name == "vlm_verify":
            return ToolResult(json.dumps({
                "ok": True,
                "verified": True,
                "semantic_visual_verified": True,
                "verify_method": "semantic-vlm",
                "verify_confidence": 0.96,
                "verify_min_confidence": 0.80,
                "verdict": {"passed": True, "issues": []},
            }), is_observe=True)
        return ToolResult(json.dumps({
            "ok": True,
            "verified": True,
            "visual_change_detected": True,
        }))

    monkeypatch.setattr(runner, "_dispatch_with_fast_visual_wrap", dispatch)
    assert runner._handle_tool_calls([_tool_call(
        "effect", "draw_strokes", {"normalized_strokes": _first_paths()},
    )]) is None
    assert runner._continuous_semantic_progress.snapshot.effect_generation == 1

    assert runner._handle_tool_calls([_tool_call(
        "verify", "vlm_verify", {"criteria": "The visible request is fully satisfied."},
    )]) is None
    snapshot = runner._continuous_semantic_progress.snapshot
    assert snapshot.effect_generation == 0
    assert snapshot.semantic_stagnation is False
    assert snapshot.fresh_semantic_required is False
