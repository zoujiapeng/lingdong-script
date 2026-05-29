"""Quick test: verify condition branching generation."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

from features.flow_features import generate_flow_script

GOALS = [
    # Test 1: condition with true_branch + false_branch
    "3秒后打开记事本在里面写'hello'然后再判断屏幕是否变化,变化就锁屏,没有变化就音量调最大",
    # Test 2: condition with true_branch only
    "等待5秒检查弹窗是否出现,如果出现就点击确定",
    # Test 3: condition
    "检查网络状态,如果网络正常就打开浏览器搜索新闻,否则创建离线文件",
    # Test 4: simple condition
    "判断屏幕是否变化,变化就锁屏",
]

os.environ.setdefault("ANTHROPIC_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
os.environ.setdefault("LLM_API_BASE", "https://api.deepseek.com/v1")
os.environ.setdefault("LLM_MODEL", "deepseek-chat")

all_ok = True
for i, goal in enumerate(GOALS, 1):
    print(f"\n{'='*60}")
    print(f"Test {i}: {goal[:80]}")
    print(f"{'='*60}")
    result = generate_flow_script(goal)
    if not result.get("success"):
        err = result.get("error", result.get("result", {}).get("error", "unknown error"))
        print(f"  FAIL: {err[:120]}")
        all_ok = False
        continue
    data = result.get("result", {})
    steps = data.get("structured_steps", [])
    if not steps:
        print(f"  DEBUG result keys: {list(result.keys())}")
        print(f"  DEBUG data keys: {list(data.keys())}")
        print(f"  DEBUG pseudocode preview: {str(data.get('pseudocode', ''))[:200]}")
    has_condition = any(s.get("type") == "condition" for s in steps)
    print(f"  Steps: {len(steps)}")
    for s in steps:
        t = s.get("type")
        if t == "condition":
            cond = s.get("condition", "")
            tb = len(s.get("true_branch", []))
            fb = len(s.get("false_branch", []))
            print(f"  [CONDITION] \"{cond}\" (true: {tb} steps, false: {fb} steps)")
        else:
            detail = s.get("target") or s.get("text") or s.get("duration") or ""
            print(f"  - {t}: {detail}")
    if has_condition:
        print(f"  PASS: contains condition branching")
    else:
        print(f"  FAIL: no condition step -- flat only")
        all_ok = False

print(f"\n{'='*60}")
print(f"{'ALL PASS' if all_ok else 'SOME FAILED'}")
sys.exit(0 if all_ok else 1)
