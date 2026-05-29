"""Debug: trace agent.run() step by step"""
import sys, os, traceback, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
os.environ["PYTHONIOENCODING"] = "utf-8"

from features.registry import registry
import features._all
t = [s for s in registry.all() if not s.deprecated and not s.hidden]
print(f"Loaded {len(t)} tools", flush=True)

from agent.agent import LingdongAgent

agent = LingdongAgent(
    api_key=os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", ""),
    model="mimo-v2-omni",
    multimodal=True, auto_learn=True, omniparser_enabled=False,
)
print("Agent created", flush=True)

# Step through run() manually
try:
    print("Step 1: _ensure_registry...", flush=True)
    agent._ensure_registry()
    print(f"  prompt: {len(agent._system_prompt)} chars", flush=True)

    print("Step 2: capture_b64...", flush=True)
    b64 = agent.supervisor.capture_b64()
    print(f"  b64: {len(b64)} chars", flush=True)

    print("Step 3: _get_screen_context...", flush=True)
    ctx = agent._get_screen_context()
    print(f"  ctx: {len(ctx)} chars: {ctx[:100]}", flush=True)

    print("Step 4: First LLM call...", flush=True)
    from agent.agent import SYSTEM_PROMPT_TEXT
    sp = SYSTEM_PROMPT_TEXT.format(tool_count=len(t), tools_text="<test>")
    messages = [{"role": "system", "content": sp}]
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": "任务目标: 打开记事本输入hello\n\n当前状态:\n测试状态"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64[:100]}"}},
        ],
    })
    raw = agent._call_llm(messages)
    if raw:
        content = raw["choices"][0]["message"].get("content", "")
        print(f"  LLM OK: {content[:200]}", flush=True)
    else:
        print("  LLM returned None", flush=True)

except Exception:
    traceback.print_exc()
    sys.stdout.flush()

print("Done", flush=True)
