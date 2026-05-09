"""
DSL 执行功能注册
lobster_run_dsl / lobster_run_dsl_sync — 让 Claude Code 通过 MCP 直接执行 DSL
核心价值：一次性生成 DSL → 本地引擎循环执行，LOOP/WAIT/IF 不消耗 token
"""
from __future__ import annotations
import json
import os
from pathlib import Path

from features.registry import feature, P, TC, FeatureCategory as F

# 注意：这个文件只注册功能规格，实际执行逻辑在 engine/executor.py
# MCP Server 会本地执行这两个功能（不代理到后端），实现免 token 执行


@feature(
    name="run_dsl",
    display_name="执行 DSL",
    description="【免 token 执行】执行 Lobster DSL 脚本。在本地引擎运行 LOOP/WAIT/IF 等控制流不消耗 Claude API token。适用于：已生成完整 DSL 后执行",
    category=F.CONTROL,
    params=[
        P("dsl", "str", "DSL 脚本内容，多行文本", example="CLICK 开始\nWAIT 2\nCLICK 确认"),
        P("task_id", "str", "任务ID（可选，自动生成）", required=False, default=""),
        P("max_loops", "int", "最大循环次数", required=False, default=100),
        P("timeout", "number", "超时秒数", required=False, default=60),
    ],
    returns="dict{task_id, state, success} - 执行结果",
    dsl_keyword=None,
    examples=[
        "lobster call run_dsl dsl='CLICK 开始\\nWAIT 2\\nCLICK 确认'",
    ],
    test_cases=[
        TC("simple_click", {"dsl": "CLICK test"}, "success", skip_in_ci=True),
    ],
    tags=["core", "dsl", "execution"],
)
def run_dsl(dsl: str, task_id: str = "", max_loops: int = 100, timeout: float = 60) -> dict:
    """通过 DSLExecutor 执行 DSL。

    自动创建 DSLExecutor 实例并传入基于 registry.execute 的 action_handler，
    使得 run_dsl 可通过 @feature 注册表直接执行。
    """
    from engine.executor import DSLExecutor, ExecutionContext
    from features.action_map import make_action_handler
    executor = DSLExecutor(action_handler=make_action_handler())
    ctx = ExecutionContext(max_loops=max_loops, timeout=timeout)
    return executor.run_dsl_sync(dsl, ctx=ctx)


@feature(
    name="run_dsl_sync",
    display_name="同步执行 DSL",
    description="【免 token 执行】同步执行 DSL 并阻塞等待执行完毕返回完整日志。适用于短流程或需要获取完整执行结果的场景",
    category=F.CONTROL,
    params=[
        P("dsl", "str", "DSL 脚本内容", example="CLICK 开始\nWAIT 完成"),
        P("max_loops", "int", "最大循环次数", required=False, default=10),
        P("retry_limit", "int", "失败重试次数", required=False, default=1),
        P("timeout", "number", "单步超时秒数", required=False, default=30),
    ],
    returns="dict{success, logs, elapsed} - 执行结果和日志",
    dsl_keyword=None,
    examples=[
        "lobster call run_dsl_sync dsl='CLICK 开始\\nWAIT 完成'",
    ],
    test_cases=[
        TC("simple_dsl", {"dsl": "CLICK test"}, "success", skip_in_ci=True),
    ],
    tags=["core", "dsl", "execution"],
)
def run_dsl_sync(dsl: str, max_loops: int = 10, retry_limit: int = 1, timeout: float = 30) -> dict:
    """同步执行 DSL。

    自动创建 DSLExecutor 实例并传入基于 registry.execute 的 action_handler，
    使得 run_dsl_sync 可通过 @feature 注册表直接执行。
    """
    from engine.executor import DSLExecutor, ExecutionContext
    from features.action_map import make_action_handler
    executor = DSLExecutor(action_handler=make_action_handler())
    ctx = ExecutionContext(max_loops=max_loops, retry_limit=retry_limit, timeout=timeout)
    return executor.run_dsl_sync(dsl, ctx=ctx)


@feature(
    name="run_script",
    display_name="运行子脚本",
    description="【DSL·调用】在 DSL 中调用另一个已注册的子流程/子脚本。需要引擎上下文支持，MCP 直接调用将返回模拟成功",
    category=F.CONTROL,
    params=[
        P("name", "str", "子脚本名称", example="login_flow"),
        P("args", "str", "传递给子脚本的参数（可选）", required=False, default=""),
    ],
    returns="bool - 是否成功调用",
    dsl_keyword="CALL",
    dsl_template="CALL {name}",
    flow_type="sub_script",
    examples=["CALL login_flow", "CALL check_update"],
    tags=["dsl", "control"],
)
def run_script(name: str, args: str = "") -> bool:
    """查找并执行子流程脚本。搜索 templates/ 目录下的 .flow 文件，找到后转换为 DSL 并执行。"""
    base_dir = Path(__file__).resolve().parent.parent.parent  # 项目根
    search_dirs = [
        base_dir / "templates",
        base_dir / "flows",
        base_dir,
    ]

    flow_path = None
    for sd in search_dirs:
        if not sd.exists():
            continue
        # Try exact match first
        candidates = [
            sd / f"{name}.flow",
            sd / f"{name}.dsl",
            sd / name,
        ]
        for c in candidates:
            if c.is_file():
                flow_path = c
                break
        if flow_path:
            break
        # Try glob match
        for ext in ("*.flow", "*.dsl"):
            for fp in sorted(sd.glob(ext)):
                if name.lower() in fp.stem.lower():
                    flow_path = fp
                    break
            if flow_path:
                break

    if not flow_path:
        return False

    try:
        content = flow_path.read_text(encoding="utf-8")
        # Try to convert flow JSON to DSL if it's a FlowScript
        if flow_path.suffix == ".flow":
            try:
                from engine.dsl_flow_converter import flow_to_dsl
                script = json.loads(content)
                dsl = flow_to_dsl(script)
            except (json.JSONDecodeError, ImportError, Exception):
                # Not a valid FlowScript, treat as raw DSL
                dsl = content
        else:
            dsl = content

        from engine.executor import DSLExecutor
        executor = DSLExecutor(None)
        result = executor.run_dsl_sync(dsl)
        return isinstance(result, dict) and result.get("success", False)
    except Exception:
        return False


@feature(
    name="dsl_optimize_analyze",
    display_name="DSL 优化分析",
    description="【Telemetry 驱动】分析功能调用 telemetry 数据，自动给出优化建议（超时调整、替代功能、失败模式）。适用于：性能调优和可靠性改进",
    category=F.DEBUG,
    params=[
        P("feature", "str", "功能名（可选，默认分析全部）", required=False, default=""),
    ],
    returns="dict{overall, suggestions, failure_patterns} - 优化分析报告",
    dsl_keyword=None,
    tags=["telemetry", "optimizer", "debug"],
)
def dsl_optimize_analyze(feature: str = "") -> dict:
    """分析 telemetry 数据并返回优化建议"""
    try:
        from engine.optimizer import TelemetryAnalyzer
        analyzer = TelemetryAnalyzer()
        result = {}
        if feature:
            result["feature_analysis"] = analyzer.analyze_feature(feature)
        else:
            result["analysis"] = analyzer.analyze_all()
            result["suggestions"] = analyzer.suggest_optimizations()
            result["failure_patterns"] = analyzer.detect_failure_patterns()
        return result
    except ImportError as e:
        return {"success": False, "error": f"Optimizer 不可用: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
