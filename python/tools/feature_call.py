"""
通过 stdin/out 持续调用 @feature（支持持久化状态）。

输入: 每行一个 JSON {"name": "feature_name", "params": {...}}
输出: 每行一个 JSON (feature 返回结果)
空行 → {"type":"pong"}

设计为长驻进程，适合维护浏览器等需要保持全局状态的 feature。
Electron 主进程通过 spawn + stdin/stdout 通信。
"""
import sys, json, threading

sys.path.insert(0, 'python')
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
sys.stdin = open(sys.stdin.fileno(), mode='r', encoding='utf-8', buffering=1)

from features._all import *
from features.registry import registry


def handle_line(line: str) -> str:
    line = line.strip()
    if not line:
        return json.dumps({"type": "pong"}, ensure_ascii=False)
    try:
        data = json.loads(line)
        result = registry.execute(data["name"], **data.get("params", {}))
        # Wrap feature result with metadata
        return json.dumps({
            "type": "result",
            "feature": data["name"],
            "result": result,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False)


def main():
    # Signal readiness
    sys.stdout.write(json.dumps({"type": "ready"}) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        response = handle_line(line)
        sys.stdout.write(response + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
