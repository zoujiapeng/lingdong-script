"""
QQ Agent 实战测试 — 直接给目标让 Agent 自己规划执行。

目标: 打开 QQ → 找到联系人"葛" → 发送消息
模式: text_protocol (MIMO V2 Omni)
"""

import json
import logging
import os
import sys
import time

# 确保能找到 python/ 目录
_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "python"))
if _PATH not in sys.path:
    sys.path.insert(0, _PATH)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)

# 抑制多余的日志
logging.getLogger("agent").setLevel(logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# MIMO API 配置 — 从环境变量读取
API_KEY = os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.xiaomimimo.com/v1")
MODEL = os.environ.get("VLM_MODEL", "mimo-v2-omni")


def main():
    # 确保加载 feature 注册表
    from features.registry import registry
    import features._all  # noqa — 注册所有工具

    all_tools = [s for s in registry.all() if not s.deprecated and not s.hidden]
    print(f"已加载 {len(all_tools)} 个工具")

    # 创建 Agent
    from agent.agent import LingdongAgent

    agent = LingdongAgent(
        api_key=API_KEY,
        api_base=API_BASE,
        model=MODEL,
        event_callback=lambda d: print(f"  [事件] {d.get('type', '?')}: {json.dumps(d, ensure_ascii=False)[:200]}"),
        multimodal=True,
        auto_learn=True,
        omniparser_enabled=False,
    )

    goal = "打开QQ，找到联系人'葛'，给他发一条消息，内容是'你好，好久不见，最近怎么样？'"

    print(f"\n{'='*60}")
    print(f"目标: {goal}")
    print(f"模型: {MODEL}")
    print(f"API:  {API_BASE}")
    print(f"{'='*60}\n")

    start = time.time()
    try:
        result = agent.run(goal, mode="text_protocol")
        elapsed = time.time() - start
        print(f"\n{'='*60}")
        print(f"完成! 耗时: {elapsed:.1f}s")
        print(f"成功: {result.get('success')}")
        print(f"总结: {result.get('summary', '')}")
        print(f"步数: {result.get('total_steps', 0)}")
        if not result.get("success"):
            print(f"原因: {result.get('fail_reason', '')}")
        print(f"{'='*60}")
    except Exception as e:
        elapsed = time.time() - start
        print(f"\n[错误] 耗时 {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
