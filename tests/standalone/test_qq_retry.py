"""
QQ 重测 — Agent 已登录的 QQ 中搜联系人发消息
"""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
os.environ["PYTHONIOENCODING"] = "utf-8"

from features.registry import registry
import features._all

all_tools = [s for s in registry.all() if not s.deprecated and not s.hidden]
print(f"Loaded {len(all_tools)} tools")

from agent.agent import LingdongAgent

agent = LingdongAgent(
    api_key=os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", ""),
    model="mimo-v2-omni",
    multimodal=True,
    auto_learn=True,
    omniparser_enabled=False,
    event_callback=lambda d: print(f"  EVENT: {d.get('type')}: {json.dumps(d, ensure_ascii=False)[:150]}"),
)

goal = "QQ已经打开并登录好了。在QQ中找到联系人'葛瑞念'，给他发送消息'你好，好久不见，最近怎么样？'"
print(f"\nGoal: {goal}")
sys.stdout.flush()

start = time.time()
result = agent.run(goal, mode="text_protocol")
elapsed = time.time() - start
print(f"\n{'='*60}")
print(f"Done! 耗时: {elapsed:.1f}s")
print(f"成功: {result.get('success')}")
print(f"总结: {result.get('summary', '')}")
print(f"步数: {result.get('total_steps', 0)}")
if not result.get('success'):
    print(f"原因: {result.get('fail_reason', '')}")
