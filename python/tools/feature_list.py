"""
列出所有已注册的 @feature（供 Electron IPC / HTTP API 调用）。
用法: python tools/feature_list.py
"""
import sys, json

sys.path.insert(0, 'python')
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

from features._all import *
from features.registry import registry

features = []
for f in registry.all():
    if f.hidden or f.deprecated:
        continue
    features.append({
        "name": f.name,
        "display_name": f.display_name,
        "description": f.description,
        "category": f.category.value,
        "params": [{"name": p.name, "type": p.type, "description": p.description,
                     "required": p.required, "default": p.default, "example": p.example} for p in f.params],
        "returns": f.returns,
        "deprecated": f.deprecated,
        "tags": f.tags,
    })

print(json.dumps(features, ensure_ascii=False))
sys.stdout.flush()
