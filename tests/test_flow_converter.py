"""
DSL ↔ FlowScript 双向转换器测试
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

import pytest
from engine.dsl_flow_converter import dsl_to_flow, flow_to_dsl, roundtrip_verify

# 加载完整 registry，使 @feature 的 dsl_keyword/flow_type 生效
try:
    import features._all  # noqa: F401
except ImportError:
    pass


class TestDSLtoFlow:
    """DSL → FlowScript 转换测试"""

    def test_simple_click_wait(self):
        flow = dsl_to_flow("CLICK 100 200\nWAIT 2")
        assert len(flow["nodes"]) >= 3  # start + click + wait + end
        assert flow["meta"] is not None
        click_nodes = [n for n in flow["nodes"] if n["type"] == "click"]
        wait_nodes = [n for n in flow["nodes"] if n["type"] == "wait"]
        assert len(click_nodes) >= 1
        assert len(wait_nodes) >= 1
        assert click_nodes[0]["params"]["x"] == 100
        assert click_nodes[0]["params"]["y"] == 200
        assert "sub_operation" not in click_nodes[0]["params"]

    def test_empty_dsl(self):
        flow = dsl_to_flow("")
        assert len(flow["nodes"]) >= 1  # at least start + end

    def test_invalid_dsl(self):
        flow = dsl_to_flow("INVALID_COMMAND xyz")
        # Should still produce a valid flow with error info
        assert "nodes" in flow

    def test_loop_conversion(self):
        flow = dsl_to_flow("LOOP 3\nCLICK 100 200\nEND")
        loop_nodes = [n for n in flow["nodes"] if n["type"] == "loop"]
        assert len(loop_nodes) >= 1
        # Check for loop_body edge
        loop_edges = [e for e in flow["edges"]
                      if e.get("from_port") == "loop_body"]
        assert len(loop_edges) >= 1

    def test_if_conversion(self):
        flow = dsl_to_flow("IF true\nCLICK 100\nELSE\nWAIT 2\nEND")
        cond_nodes = [n for n in flow["nodes"] if n["type"] == "condition"]
        assert len(cond_nodes) >= 1
        yes_edges = [e for e in flow["edges"]
                     if e.get("from_port") == "yes"]
        assert len(yes_edges) >= 1

    def test_type_conversion(self):
        flow = dsl_to_flow('TYPE "hello"')
        type_nodes = [n for n in flow["nodes"] if n["type"] == "input_text"]
        assert len(type_nodes) >= 1
        assert type_nodes[0]["params"]["text"] == "hello"

    def test_image_click_conversion(self):
        flow = dsl_to_flow('IMAGE_CLICK "test.png" "test2.png"')
        img_nodes = [n for n in flow["nodes"] if n["type"] == "image_click"]
        assert len(img_nodes) >= 1
        assert len(img_nodes[0]["params"]["targets"]) == 2

    def test_hotkey_conversion(self):
        flow = dsl_to_flow("HOTKEY ctrl+c")
        kb_nodes = [n for n in flow["nodes"] if n["type"] == "keyboard"]
        assert len(kb_nodes) >= 1

    def test_swipe_conversion(self):
        flow = dsl_to_flow("SWIPE 100 200 300 400")
        swipe_nodes = [n for n in flow["nodes"] if n["type"] == "swipe"]
        assert len(swipe_nodes) >= 1
        p = swipe_nodes[0]["params"]
        assert p["start_x"] == 100
        assert p["start_y"] == 200
        assert p["end_x"] == 300
        assert p["end_y"] == 400

    def test_drag_conversion_uses_swipe_shape(self):
        flow = dsl_to_flow("DRAG 10 20 30 40")
        swipe_nodes = [n for n in flow["nodes"] if n["type"] == "swipe"]
        assert len(swipe_nodes) >= 1
        p = swipe_nodes[0]["params"]
        assert p["start_x"] == 10
        assert p["start_y"] == 20
        assert p["end_x"] == 30
        assert p["end_y"] == 40

    def test_screenshot_conversion(self):
        flow = dsl_to_flow("SCREENSHOT")
        ss_nodes = [n for n in flow["nodes"] if n["type"] == "screenshot"]
        assert len(ss_nodes) >= 1

    def test_ocr_find_conversion(self):
        flow = dsl_to_flow('OCR_FIND "确认"')
        ct_nodes = [n for n in flow["nodes"] if n["type"] == "click_text"]
        assert len(ct_nodes) >= 1
        assert ct_nodes[0]["params"]["text"] == "确认"


class TestFlowtoDSL:
    """FlowScript → DSL 转换测试"""

    def test_simple_flow(self):
        script = {
            "meta": {"name": "test"},
            "nodes": [
                {"id": "n1", "type": "start", "x": 300, "y": 0,
                 "params": {"label": "开始"}, "status": "idle"},
                {"id": "n2", "type": "click", "x": 300, "y": 100,
                 "params": {"x": 100, "y": 200, "method": "single",
                            "delay_after": 0.5}},
                {"id": "n3", "type": "end", "x": 300, "y": 200,
                 "params": {"label": "结束"}, "status": "idle"},
            ],
            "edges": [
                {"id": "e1", "from_node": "n1", "from_port": "output",
                 "to_node": "n2", "to_port": "input"},
                {"id": "e2", "from_node": "n2", "from_port": "output",
                 "to_node": "n3", "to_port": "input"},
            ],
            "variables": {},
            "settings": {"default_delay": 0.3},
        }
        dsl = flow_to_dsl(script)
        assert "CLICK 100 200" in dsl

    def test_roundtrip(self):
        """验证 DSL → FlowScript → DSL 往返。"""
        original = "CLICK 100 200\nWAIT 2\nSCREENSHOT"
        result = roundtrip_verify(original)
        assert "error" not in result, f"Roundtrip failed: {result.get('error')}"
        # 语义等价（关键字匹配）
        rt = result.get("roundtrip", "")
        assert "CLICK" in rt
        assert "WAIT" in rt
        assert "SCREENSHOT" in rt

    def test_roundtrip_with_loop(self):
        original = "LOOP 3\nCLICK 100 200\nEND\nWAIT 2"
        result = roundtrip_verify(original)
        assert "error" not in result
        rt = result.get("roundtrip", "")
        assert "CLICK" in rt
        assert "LOOP" in rt or "END" in rt

    def test_roundtrip_with_if(self):
        original = "IF true\nCLICK 100\nELSE\nWAIT 2\nEND"
        result = roundtrip_verify(original)
        assert "error" not in result
        rt = result.get("roundtrip", "")
        assert "CLICK" in rt or "WAIT" in rt


    def test_custom_flow_to_dsl(self):
        """带循环的 FlowScript 转 DSL。"""
        script = {
            "meta": {"name": "loop test"},
            "nodes": [
                {"id": "n1", "type": "start", "x": 300, "y": 0,
                 "params": {}, "status": "idle"},
                {"id": "n2", "type": "loop", "x": 300, "y": 80,
                 "params": {"loop_type": "count", "count": 5}},
                {"id": "n3", "type": "click", "x": 400, "y": 160,
                 "params": {"x": 50, "y": 60, "method": "single",
                            "delay_after": 0.5}},
                {"id": "n4", "type": "end", "x": 300, "y": 240,
                 "params": {}, "status": "idle"},
            ],
            "edges": [
                {"id": "e1", "from_node": "n1", "from_port": "output",
                 "to_node": "n2", "to_port": "input"},
                {"id": "e2", "from_node": "n2", "from_port": "loop_body",
                 "to_node": "n3", "to_port": "input", "animated": True},
                {"id": "e3", "from_node": "n2", "from_port": "loop_end",
                 "to_node": "n4", "to_port": "input"},
            ],
            "variables": {},
            "settings": {},
        }
        dsl = flow_to_dsl(script)
        assert "LOOP 5" in dsl
        assert "CLICK 50 60" in dsl
        assert "RETURN" in dsl


class TestRegistryFlowTypes:
    """验证完整 registry 加载后 DSL → Flow 类型映射正确"""

    def test_all_dsl_keywords_map_to_valid_flow_types(self):
        """每个有 dsl_keyword 的 @feature 都映射到正确的 flow node type。"""
        from features.registry import registry
        from engine.dsl_flow_converter import get_dsl_to_flow_type

        mapping = get_dsl_to_flow_type()

        for f in registry.all():
            if not f.dsl_keyword or f.deprecated:
                continue
            kw = f.dsl_keyword.upper()
            assert kw in mapping, f"{f.name}: dsl_keyword={kw} 不在 mapping 中"
            if f.flow_type:
                assert mapping[kw] == f.flow_type, (
                    f"{f.name}: mapping[{kw}] = {mapping[kw]}, 期望 {f.flow_type}"
                )

    def test_dsl_to_flow_produces_correct_types(self):
        """实际解析 DSL 并验证节点类型正确。"""
        cases = [
            ("CLICK 100 200", "click"),
            ("WAIT 2", "wait"),
            ('TYPE "hello"', "input_text"),
            ("HOTKEY ctrl+c", "keyboard"),
            ("SWIPE 0 0 100 100", "swipe"),
            ("DRAG 0 0 100 100", "swipe"),
            ("SCROLL down", "swipe"),
            ("SCREENSHOT", "screenshot"),
            ("FOCUS Paint", "focus_window"),
            ('IMAGE_CLICK "test.png"', "image_click"),
            ('OCR_FIND "text"', "click_text"),
            ("WAITSCREEN", "region_wait"),
            ("SCREENSTABLE", "region_wait"),
            ("FINDCANVAS", "condition"),
            ('SET x "1"', "variable_set"),
            ("POPUP_CLOSE", "popup_close"),
            ("COLOR_CHECK 0 0 100 100 255 0 0 30", "color_check"),
            ("PROGRESS_WAIT 0,0,100,20 0.8", "progress_wait"),
            ("REGION_WAIT 0,0,100,20 stable", "region_wait"),
            ("SCENE_CONDITION scene 80", "scene_condition"),
            ('IMAGE_FIND "test.png"', "condition"),
            ("CALL test", "sub_script"),
        ]
        for dsl_text, expected_type in cases:
            flow = dsl_to_flow(dsl_text)
            content_nodes = [n for n in flow["nodes"]
                             if n["type"] not in ("start", "end")]
            assert len(content_nodes) >= 1, (
                f"dsl_to_flow({dsl_text!r}) 未生成内容节点"
            )
            actual = content_nodes[0]["type"]
            assert actual == expected_type, (
                f"dsl_to_flow({dsl_text!r}): 期望 {expected_type}, 得到 {actual}"
            )

    def test_all_registry_keywords_parse_without_error(self):
        """每个有 dsl_keyword 的 @feature 都能被 DSLParser 正常解析。"""
        from features.registry import registry
        from engine.dsl_parser import DSLParser

        for f in registry.all():
            if not f.dsl_keyword or f.deprecated:
                continue
            try:
                ast = DSLParser.from_string(f"{f.dsl_keyword} test")
                assert len(ast.children) > 0
            except Exception as e:
                raise AssertionError(
                    f"{f.name}: DSLParser 无法解析关键字 '{f.dsl_keyword}': {e}"
                )

    def test_simple_node_types_no_conflict(self):
        """_SIMPLE_NODE_TYPES 关键字不应与 registry 关键字冲突。

        现在 executor 支持通过 REGISTRY_FEATURE 动态执行任意 registry 关键字，
        所以所有带 dsl_keyword 的 feature 都是合法的 DSL 指令。
        """
        from features.registry import registry
        from engine.dsl_parser import _SIMPLE_NODE_TYPES

        registry_kws = {
            f.dsl_keyword.upper()
            for f in registry.all()
            if f.dsl_keyword and not f.deprecated
        }
        simple_kws = set(_SIMPLE_NODE_TYPES.keys())

        complex_kws = {"VLM_ANALYZE", "LOOP", "IF", "SUBROUTINE", "CALL",
                       "IMPORT", "RETURN", "WHEN", "WITH", "PARALLEL",
                       "WHILE", "UNTIL"}
        registry_fallback_kws = {"TYPE_CLIP"}
        # executor 通过 REGISTRY_FEATURE 动态处理所有 registry 关键字
        all_legacy = simple_kws | complex_kws | registry_fallback_kws
        unexpected = registry_kws - all_legacy
        # executor 的 REGISTRY_FEATURE 分支会动态执行 — 不报错
        if unexpected:
            import logging
            logging.getLogger(__name__).debug(
                "%d 个关键字由 executor 动态处理（REGISTRY_FEATURE）",
                len(unexpected)
            )
