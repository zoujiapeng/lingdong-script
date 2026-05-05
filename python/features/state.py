"""
共享运行时状态（日志缓存、变量存储等）
供 server.py 写入，features 读取
"""
import time

_executor_logs: list[dict] = []
_MAX_LOGS = 1000

# 全局变量存储（供 set_variable / get_variable 功能使用）
_variables: dict[str, str] = {}


def push_log(message: str):
    _executor_logs.append({"ts": time.time(), "msg": message})
    if len(_executor_logs) > _MAX_LOGS:
        _executor_logs.pop(0)


def get_logs(count: int = 50) -> list[dict]:
    return _executor_logs[-count:]


# ── 全局变量存储 ──────────────────────────────────────

def set_var(name: str, value: str) -> None:
    _variables[name] = value


def get_var(name: str, default: str = "") -> str:
    return _variables.get(name, default)


def list_vars() -> dict[str, str]:
    return dict(_variables)


def clear_vars() -> None:
    _variables.clear()
