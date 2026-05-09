"""
引擎入口：Electron 主进程通过子进程调用。
读取 stdin 的 FlowScript JSON → 转换 DSL → 执行 → 输出 JSON 事件到 stdout。
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

# 确保能找到 python/ 下的模块
_self_dir = Path(__file__).resolve().parent
_python_root = _self_dir.parent  # python/
if str(_python_root) not in sys.path:
    sys.path.insert(0, str(_python_root))

from engine.executor import DSLExecutor
from engine.dsl_flow_converter import flow_to_dsl


# ── Action Handler 初始化 ──────────────────────────────────────
_action_handler = None
try:
    from interaction.actions import ActionHandler
    _action_handler = ActionHandler()
except ImportError as e:
    pass  # simulate mode


def emit(msg: dict):
    """向 stdout 输出 JSON 事件（Electron 主进程读取）。"""
    line = json.dumps(msg, ensure_ascii=False, default=str)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def execute_flow(script: dict) -> dict:
    """FlowScript → DSL → DSLExecutor 同步执行。"""
    try:
        dsl_text = flow_to_dsl(script)
        if not dsl_text.strip():
            return {"success": False, "error": "转换后的 DSL 为空"}
        emit({"type": "log", "message": f"DSL:\n{dsl_text}"})
    except Exception as e:
        return {"success": False, "error": f"Flow → DSL 转换失败: {e}"}

    executor = DSLExecutor(action_handler=_action_handler)
    mode = "真实" if _action_handler else "模拟"
    emit({"type": "log", "message": f"引擎启动 ({mode}模式)"})

    # 注册事件回调 → stdout
    executor.on("node_start", lambda **kw: emit({"type": "node_start", **kw}))
    executor.on("node_done", lambda **kw: emit({"type": "node_done", **kw}))
    executor.on("error", lambda **kw: emit({"type": "error", **kw}))
    executor.on("state_change", lambda **kw: emit({"type": "state_change", **kw}))
    executor.on("log", lambda **kw: emit({"type": "log", **kw}))

    try:
        result = executor.run_dsl_sync(dsl_text)
        if result.get("success"):
            emit({"type": "execution_done", "status": "completed"})
        else:
            emit({"type": "execution_done", "status": "error", "error": result.get("error")})
        return result
    except Exception as e:
        emit({"type": "execution_done", "status": "error", "error": str(e)})
        return {"success": False, "error": str(e)}


def main():
    emit({"type": "log", "message": "引擎进程启动"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        # 支持控制指令
        if line.startswith("{"):
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                emit({"type": "error", "message": f"JSON 解析失败: {line[:200]}"})
                continue

            # 停止指令
            if isinstance(msg, dict) and msg.get("type") == "stop":
                emit({"type": "log", "message": "引擎收到停止指令"})
                break

            # FlowScript JSON
            if isinstance(msg, dict) and ("nodes" in msg or "dsl" in msg):
                if "dsl" in msg and isinstance(msg["dsl"], str):
                    # 直接 DSL 文本
                    dsl_text = msg["dsl"]
                    executor = DSLExecutor(action_handler=_action_handler)
                    executor.on("node_start", lambda **kw: emit({"type": "node_start", **kw}))
                    executor.on("node_done", lambda **kw: emit({"type": "node_done", **kw}))
                    executor.on("error", lambda **kw: emit({"type": "error", **kw}))
                    executor.on("state_change", lambda **kw: emit({"type": "state_change", **kw}))
                    executor.on("log", lambda **kw: emit({"type": "log", **kw}))
                    try:
                        executor.run_dsl_sync(dsl_text)
                        emit({"type": "execution_done", "status": "completed"})
                    except Exception as e:
                        emit({"type": "execution_done", "status": "error", "error": str(e)})
                else:
                    execute_flow(msg)
                continue

        # 裸 DSL 文本
        executor = DSLExecutor(action_handler=_action_handler)
        executor.on("node_start", lambda **kw: emit({"type": "node_start", **kw}))
        executor.on("node_done", lambda **kw: emit({"type": "node_done", **kw}))
        executor.on("error", lambda **kw: emit({"type": "error", **kw}))
        executor.on("state_change", lambda **kw: emit({"type": "state_change", **kw}))
        executor.on("log", lambda **kw: emit({"type": "log", **kw}))
        try:
            executor.run_dsl_sync(line)
            emit({"type": "execution_done", "status": "completed"})
        except Exception as e:
            emit({"type": "execution_done", "status": "error", "error": str(e)})


if __name__ == "__main__":
    main()
