"""
DSL ↔ FlowScript 双向转换器测试
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

import pytest
from engine.dsl_flow_converter import dsl_to_flow, flow_to_dsl, roundtrip_verify


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
