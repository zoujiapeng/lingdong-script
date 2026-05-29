"""
Agent 自主聊天测试 — 找葛瑞念聊天并自动回复
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

# 目标：找葛瑞念聊天，自动回复对方的消息
goal = (
    "打开QQ，找到联系人'葛瑞念'，给他发一条消息说'在吗？好久没聊天了'\n"
    "然后等待他的回复，根据他回复的内容自动回复他。"
    "这样来回对话2-3轮。每轮你都要：读取他的新消息 → 根据内容生成合理回复 → 发送"
)

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
