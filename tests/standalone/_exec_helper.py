"""Subprocess helper for test_features_comprehensive - isolates COM crashes."""
import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from features._all import *
from features.registry import registry

name = sys.argv[1]
params = json.loads(sys.argv[2])

try:
    result = registry.execute(name, **params)
    print("RESULT:" + json.dumps(result, ensure_ascii=False), flush=True)
except Exception as e:
    print("ERROR:" + str(e)[:300], flush=True)
