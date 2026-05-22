"""
TriggerEngine → stdout JSON 事件桥接
将 TriggerEngine 的状态变更通过 emit() 函数输出到 stdout，
Electron 主进程 engine.ts 读取后转发到渲染进程。

使用方式:
  from engine.triggers import get_trigger_engine
  from engine.trigger_bridge import setup_trigger_bridge
  engine = get_trigger_engine()
  setup_trigger_bridge(engine)  # 使用默认 sys.stdout emit
"""
import json
import sys
from typing import Callable, Optional


def setup_trigger_bridge(engine, emit_fn: Optional[Callable] = None):
    """为 TriggerEngine 附加状态变更回调。

    每次触发器注册/停止/触发时，emit_fn 会被调用并传入当前所有触发器状态。
    Args:
        engine: TriggerEngine 实例
        emit_fn: 接收 dict 并输出的函数。默认写入 sys.stdout JSON line。
    Returns:
        engine 本身（可链式调用）
    """
    if emit_fn is None:
        def emit_fn(msg: dict):
            line = json.dumps(msg, ensure_ascii=False, default=str)
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def on_status():
        emit_fn({
            "type": "trigger_status",
            "triggers": engine.list_instances(),
        })

    engine.set_status_callback(on_status)

    # 立即发送一次初始状态
    on_status()

    return engine
