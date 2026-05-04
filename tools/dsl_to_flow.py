# -*- coding: utf-8 -*-
"""DSL -> FlowScript 转换桥接器（被 Electron IPC 调用）

用法：
  echo 'CLICK 100 200' | python tools/dsl_to_flow.py
  python tools/dsl_to_flow.py < file.dsl
  # 输出 FlowScript JSON
"""
import sys
import os
import json

# 强制 UTF-8 输出，防止 Windows GBK 编码导致中文乱码
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))
from engine.dsl_flow_converter import dsl_to_flow  # noqa: E402


def main():
    dsl_text = sys.stdin.read().strip()
    if not dsl_text:
        print(json.dumps({"error": "DSL 文本为空"}))
        sys.exit(1)
    try:
        result = dsl_to_flow(dsl_text)
        # 确保包含必要字段
        if "nodes" not in result:
            result["nodes"] = []
        if "edges" not in result:
            result["edges"] = []
        if "variables" not in result:
            result["variables"] = {}
        if "settings" not in result:
            result["settings"] = {"default_delay": 0.5, "error_handling": "stop"}
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
