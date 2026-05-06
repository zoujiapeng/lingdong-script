"""
通过 spawnPython 执行 DSL 文本。
用法: python tools/run_dsl.py < dsl_text.txt
      echo "CLICK 100 200" | python tools/run_dsl.py
"""
import sys, json

sys.path.insert(0, 'python')
from engine.executor import DSLExecutor

if __name__ == '__main__':
    dsl = sys.stdin.read()
    e = DSLExecutor(None)
    try:
        e.run_dsl_sync(dsl)
        print(json.dumps({"success": True}))
    except Exception as ex:
        print(json.dumps({"success": False, "error": str(ex)}))
