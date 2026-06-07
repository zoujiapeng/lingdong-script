import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from agent.agent import LingdongAgent
from agent.text_protocol import parse_response, parse_tool_blocks


def test_parse_ascii_tool_block():
    content = """Thought: use the current marked element.
Tool: click
Args:
  target: #3
  button: left
"""
    action, name, args = LingdongAgent._parse_response(content)
    assert parse_response(content) == (action, name, args)
    assert action == "tool"
    assert name == "click"
    assert args == {"target": "#3", "button": "left"}


def test_parse_chinese_tool_block():
    content = """思考: 点击搜索框
工具: type_text
参数:
  text: hello
  clear_first: true
"""
    action, name, args = LingdongAgent._parse_response(content)
    assert action == "tool"
    assert name == "type_text"
    assert args == {"text": "hello", "clear_first": "true"}


def test_parse_multiple_tool_blocks():
    content = """Tool: click
Args:
  target: #1

Tool: type_text
Args:
  text: hello
"""
    expected = [
        ("click", {"target": "#1"}),
        ("type_text", {"text": "hello"}),
    ]
    assert LingdongAgent._parse_all_tools(content) == expected
    assert parse_tool_blocks(content) == expected


def test_parse_done_marker():
    action, _, args = LingdongAgent._parse_response("Done:\nSummary: finished")
    assert action == "done"
    assert "finished" in args["summary"]
