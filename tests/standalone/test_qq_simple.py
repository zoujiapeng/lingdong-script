"""
Simplified QQ agent test — direct run with full crash logging.
"""
import json
import logging
import os
import sys
import time
import traceback

_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "python"))
if _PATH not in sys.path:
    sys.path.insert(0, _PATH)

# Log everything to stderr
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

API_KEY = os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
API_BASE = "https://api.xiaomimimo.com/v1"
MODEL = "mimo-v2-omni"

sys.stderr.write(f"[SETUP] Loading feature registry...\n")
sys.stderr.flush()

from features.registry import registry
import features._all

all_tools = [s for s in registry.all() if not s.deprecated and not s.hidden]
sys.stderr.write(f"[SETUP] Loaded {len(all_tools)} tools\n")
sys.stderr.flush()

sys.stderr.write(f"[SETUP] Creating agent...\n")
sys.stderr.flush()

from agent.agent import LingdongAgent

agent = LingdongAgent(
    api_key=API_KEY,
    api_base=API_BASE,
    model=MODEL,
    multimodal=True,
    auto_learn=True,
    omniparser_enabled=False,
    event_callback=lambda d: sys.stderr.write(f"  [EVENT] {d.get('type', '?')}: {json.dumps(d, ensure_ascii=False)[:200]}\n"),
)

goal = "打开QQ，找到联系人'葛'，给他发一条消息，内容是'你好，好久不见，最近怎么样？'"

sys.stderr.write(f"[START] Goal: {goal}\n")
sys.stderr.write(f"[START] Model: {MODEL}\n")
sys.stderr.flush()
sys.stdout.write(json.dumps({"type": "start", "goal": goal, "model": MODEL}) + "\n")
sys.stdout.flush()

start = time.time()
try:
    result = agent.run(goal, mode="text_protocol")
    elapsed = time.time() - start
    report = {
        "type": "done",
        "success": result.get("success"),
        "summary": result.get("summary", ""),
        "steps": result.get("total_steps", 0),
        "elapsed": round(elapsed, 1),
    }
    sys.stdout.write(json.dumps(report, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    sys.stderr.write(f"[DONE] {json.dumps(report, ensure_ascii=False)}\n")
    sys.stderr.flush()
except Exception as e:
    elapsed = time.time() - start
    err = {"type": "error", "message": str(e), "elapsed": round(elapsed, 1)}
    sys.stdout.write(json.dumps(err, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    sys.stderr.write(f"[ERROR] {e}\n")
    sys.stderr.write(traceback.format_exc() + "\n")
    sys.stderr.flush()
