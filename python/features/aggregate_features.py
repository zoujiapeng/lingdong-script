"""
聚合功能：将相关子功能合并为单一入口，通过 action 参数选择具体操作。
每个子功能标记 hidden=True，不出现在前端指令库中。
"""
from features.registry import feature, P, FeatureCategory as F


# ── 等待条件 ────────────────────────────────────────────────────────

@feature(
    name="wait_condition",
    display_name="等待条件",
    description="等待条件：等待文件/进程/网络/指定时间",
    category=F.CONTROL,
    params=[
        P("action", "str", "等待类型",
          choices={"file": "等待文件", "process": "等待进程", "network": "等待网络", "time": "等待到指定时间"}),
        P("path", "str", "文件路径（等待文件时必填）", required=False, default=""),
        P("name", "str", "进程名称（等待进程时必填）", required=False, default=""),
        P("timeout", "number", "超时秒数", required=False, default=60),
        P("target_time", "str", "目标时间 HH:MM（等待到指定时间时必填）", required=False, default=""),
        P("wait_delete", "boolean", "是否等待文件删除而非创建", required=False, default=False),
        P("wait_exit", "boolean", "是否等待进程退出而非启动", required=False, default=False),
        P("wait_disconnect", "boolean", "是否等待断开而非连接", required=False, default=False),
    ],
    returns="bool - 是否在规定时间内达到目标状态",
    tags=["timer", "wait", "aggregate"],
    dsl_keyword="",
)
def wait_condition(action="file", path="", name="", timeout=60, target_time="",
                   wait_delete=False, wait_exit=False, wait_disconnect=False):
    if action == "file":
        from features.timer_features import timer_wait_file
        return timer_wait_file(path=path, timeout=timeout, wait_delete=wait_delete)
    elif action == "process":
        from features.timer_features import timer_wait_process
        return timer_wait_process(name=name, timeout=timeout, wait_exit=wait_exit)
    elif action == "network":
        from features.timer_features import timer_wait_network
        return timer_wait_network(timeout=timeout, wait_disconnect=wait_disconnect)
    elif action == "time":
        from features.timer_features import timer_wait_time
        return timer_wait_time(target=target_time)
    return {"success": False, "error": f"未知操作: {action}"}

