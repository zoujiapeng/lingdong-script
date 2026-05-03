"""
统一入口：将 DSL 文本和 FlowScript JSON 统一路由到 DSL 执行器。

提供一致的 run() 接口，接受 DSL 或 FlowScript 两种格式，
内部统一通过 DSLExecutor 执行。
"""

from __future__ import annotations
import json
import time
import logging
from typing import Any, Optional

from engine.executor import DSLExecutor, ExecutionContext
from engine.dsl_flow_converter import dsl_to_flow, flow_to_dsl
from engine.dsl_parser import DSLParseError

logger = logging.getLogger("unified_entry")


def run(
    source: str | dict,
    variables: Optional[dict] = None,
    timeout: float = 300.0,
    action_handler=None,
) -> dict:
    """统一执行入口。

    Args:
        source: DSL 文本或 FlowScript JSON 字典
        variables: 初始变量
        timeout: 每步超时（秒）
        action_handler: 底层动作回调

    Returns:
        {"success": bool, "state": str, "error": str|None, "result": Any}
    """
    if isinstance(source, dict):
        return _run_flow(source, variables, timeout, action_handler)
    else:
        return _run_dsl(source, variables, timeout, action_handler)


def _run_dsl(
    dsl_text: str,
    variables: Optional[dict] = None,
    timeout: float = 300.0,
    action_handler=None,
) -> dict:
    """直接执行 DSL 文本。"""
    if not dsl_text or not dsl_text.strip():
        return {"success": False, "error": "DSL 文本为空", "state": "error"}

    executor = DSLExecutor(action_handler=action_handler)
    ctx = ExecutionContext(
        variables=dict(variables or {}),
        timeout=timeout,
    )
    return executor.run_dsl_sync(dsl_text, ctx)


def _run_flow(
    script: dict,
    variables: Optional[dict] = None,
    timeout: float = 300.0,
    action_handler=None,
) -> dict:
    """将 FlowScript 转 DSL 后再执行。"""
    if not script:
        return {"success": False, "error": "脚本为空", "state": "error"}

    try:
        dsl_text = flow_to_dsl(script)
    except Exception as e:
        return {"success": False, "error": f"FlowScript → DSL 转换失败: {e}", "state": "error"}

    executor = DSLExecutor(action_handler=action_handler)
    ctx = ExecutionContext(
        variables=dict(variables or {}),
        timeout=timeout,
    )
    return executor.run_dsl_sync(dsl_text, ctx)


def quick_validate(source: str | dict) -> dict:
    """快速验证 DSL/FlowScript 是否可执行，不实际执行。

    Returns:
        {"valid": bool, "format": "dsl"|"flow"|"unknown", "error": str|None}
    """
    if isinstance(source, dict):
        nodes = source.get("nodes", [])
        edges = source.get("edges", [])
        if nodes and edges:
            return {"valid": True, "format": "flow", "error": None}
        return {"valid": False, "format": "flow", "error": f"nodes={len(nodes)}, edges={len(edges)}"}
    if isinstance(source, str):
        try:
            from engine.dsl_parser import DSLParser
            parser = DSLParser()
            parser.parse(source)
            return {"valid": True, "format": "dsl", "error": None}
        except DSLParseError as e:
            return {"valid": False, "format": "dsl", "error": str(e)}
        except Exception as e:
            return {"valid": False, "format": "dsl", "error": str(e)}
    return {"valid": False, "format": "unknown", "error": f"不支持的类型: {type(source).__name__}"}
