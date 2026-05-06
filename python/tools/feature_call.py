"""
通过 stdin 调用 @feature（供 Electron IPC / HTTP API 调用）。
输入: {"name": "feature_name", "params": {...}}
输出: JSON result（由 feature 函数返回）
"""
import sys, json

sys.path.insert(0, 'python')
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

from features._all import *
from features.registry import registry

try:
    raw = sys.stdin.read()
    data = json.loads(raw)
    result = registry.execute(data["name"], **data.get("params", {}))
    print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
sys.stdout.flush()
