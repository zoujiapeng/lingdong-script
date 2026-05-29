"""Minimal agent test — check if run() works at all"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
os.environ["PYTHONIOENCODING"] = "utf-8"

from features.registry import registry
import features._all

from agent.agent import LingdongAgent

agent = LingdongAgent(
    api_key=os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", ""),
    model="mimo-v2-omni",
    multimodal=True, auto_learn=True, omniparser_enabled=False,
)

print("AGENT_CREATED", flush=True)
sys.stdout.flush()

result = agent.run("打开记事本，输入 Hello World", mode="text_protocol")
print(f"DONE success={result.get('success')} steps={result.get('total_steps',0)}", flush=True)
