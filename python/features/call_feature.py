"""调用单个 feature 并输出结果（one-shot）

用法:
  echo '{"feature":"debug_system_info"}' | python call_feature.py
  python call_feature.py '{"feature":"click_target","target":"确认"}'
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import features._all  # noqa
from features.registry import registry

if len(sys.argv) > 1:
    data = json.loads(sys.argv[1])
else:
    data = json.load(sys.stdin)

name = data.pop("feature", "")
if not name:
    result = {"success": False, "error": "missing 'feature' field"}
else:
    result = registry.execute(name, **data)

sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)
json.dump(result, sys.stdout, ensure_ascii=False)
