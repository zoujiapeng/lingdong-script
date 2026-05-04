"""Export all registered features as JSON array (one-shot)"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import features._all  # noqa: trigger @feature registration
from features.registry import registry

sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)
json.dump([f.to_dict() for f in registry.all()], sys.stdout, ensure_ascii=False)
