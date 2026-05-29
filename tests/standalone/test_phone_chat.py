"""text_protocol 模式 — QQ和我的手机聊天"""
import json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
os.environ["PYTHONIOENCODING"] = "utf-8"

from features.registry import registry
import features._all
t = [s for s in registry.all() if not s.deprecated and not s.hidden]
print(f"Loaded {len(t)} tools")
sys.stdout.flush()

from agent.agent import LingdongAgent

agent = LingdongAgent(
    api_key=os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", ""),
    model="mimo-v2-omni",
    multimodal=True, auto_learn=True, omniparser_enabled=False,
)

goal = (
    "QQ已打开并登录好了。在QQ联系人列表中找到'我的手机'（或者你自己的手机号QQ），"
    "给他发消息'测试消息，收到请回复'，"
    "等他回复后自动回复他，来回聊2-3轮。每次输入文字后都要按Enter发送"
)
print(f"Goal: {goal}")
sys.stdout.flush()

start = time.time()
try:
    result = agent.run(goal, mode="planned")
    elapsed = time.time() - start
    print(f"\nDone! {elapsed:.1f}s success={result.get('success')} summary={result.get('summary','')[:200]} steps={result.get('total_steps',0)}")
except Exception as e:
    elapsed = time.time() - start
    print(f"\nError after {elapsed:.1f}s: {e}")
    import traceback
    traceback.print_exc()
sys.stdout.flush()
