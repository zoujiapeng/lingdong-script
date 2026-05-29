"""
SoM (text-only) vs Direct (full screenshot) 对比测试。

测试目标: 打开QQ → 找到联系人"葛" → 发送消息
对比维度: 耗时 / 步骤数 / 成功率 / 成本估算

用法:
  python tests/test_compare_som_vs_direct.py [--mode som|direct|both]
"""
import json
import logging
import os
import sys
import time
import argparse

_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "python"))
if _PATH not in sys.path:
    sys.path.insert(0, _PATH)

from dotenv import load_dotenv
load_dotenv(os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".env")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(sys.stderr)])
logging.getLogger("agent").setLevel(logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

API_KEY = os.environ.get("OPENAI_API_KEY", "")
API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.xiaomimimo.com/v1")
MODEL = os.environ.get("VLM_MODEL", "mimo-v2-omni")

GOAL = "打开QQ，找到联系人'葛'，给他发一条消息，内容是'你好，好久不见，最近怎么样？'"

def run_test(mode: str, vision_mode: str) -> dict:
    """Run agent in specified mode and return results."""
    from features.registry import registry
    import features._all  # noqa

    all_tools = [s for s in registry.all() if not s.deprecated and not s.hidden]

    from agent.agent import LingdongAgent
    from agent.config import AgentConfig

    steps_log = []

    def event_cb(data):
        t = data.get("type", "")
        if t == "step_complete":
            steps_log.append({
                "step": data.get("step_num"),
                "tool": data.get("tool"),
                "success": data.get("result", {}).get("success"),
                "error": data.get("result", {}).get("error", "")[:100],
            })

    agent = LingdongAgent(
        api_key=API_KEY,
        api_base=API_BASE,
        model=MODEL,
        event_callback=event_cb,
        multimodal=False if mode == "som" else True,
        auto_learn=False,
        omniparser_enabled=False,
    )

    cfg = AgentConfig(
        llm_protocol="text",
        planning="per_step",
        vision_mode=vision_mode,
        screen_context="minimal" if mode == "som" else "full",
        on_error="retry",
        verify=True,
        fast_mode=False,
        image_dedup=True,
        multimodal="true" if mode == "direct" else "false",
    )

    print(f"\n{'='*60}")
    print(f"模式: {mode.upper()} ({vision_mode})")
    print(f"{'='*60}")

    start = time.time()
    token_estimate = 0
    try:
        result = agent.run(GOAL, agent_config=cfg)
        elapsed = time.time() - start
        success = result.get("success", False)
        total_steps = result.get("total_steps", len(steps_log))
        summary = result.get("summary", "")
        fail_reason = result.get("fail_reason", "")

        # 粗估 token: direct 模式每步 ~1MB 图片 ≈ 1000 tokens (vision)
        # som 模式只有文本 ≈ 每步 500 tokens
        if mode == "direct":
            token_estimate = total_steps * 1500  # 1000 vision + 500 text
        else:
            token_estimate = total_steps * 600   # 100 text + 500 SoM

        print(f"\n完成! 耗时: {elapsed:.1f}s")
        print(f"成功: {success}")
        print(f"步数: {total_steps}")
        print(f"粗估 tokens: ~{token_estimate}")
        if summary:
            print(f"总结: {summary[:200]}")
        if not success and fail_reason:
            print(f"失败原因: {fail_reason[:200]}")

        return {
            "mode": mode,
            "vision_mode": vision_mode,
            "success": success,
            "elapsed_s": round(elapsed, 1),
            "total_steps": total_steps,
            "token_estimate": token_estimate,
            "summary": summary[:200],
            "fail_reason": fail_reason[:200] if fail_reason else "",
            "steps": steps_log,
        }
    except Exception as e:
        elapsed = time.time() - start
        import traceback
        traceback.print_exc()
        return {
            "mode": mode,
            "vision_mode": vision_mode,
            "success": False,
            "elapsed_s": round(elapsed, 1),
            "total_steps": len(steps_log),
            "token_estimate": 0,
            "summary": "",
            "fail_reason": str(e)[:200],
            "steps": steps_log,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["som", "direct", "both"], default="both")
    args = parser.parse_args()

    print(f"API Base: {API_BASE}")
    print(f"Model: {MODEL}")
    print(f"Key: {API_KEY[:12]}...{API_KEY[-4:]}")
    print(f"Goal: {GOAL}")

    results = {}

    if args.mode in ("som", "both"):
        results["som"] = run_test("som", "som")

    if args.mode in ("direct", "both"):
        results["direct"] = run_test("direct", "direct")

    # 打印对比
    print(f"\n{'='*60}")
    print("对比总结")
    print(f"{'='*60}")
    print(f"{'指标':<20} {'SoM (文本)':<20} {'Direct (截图)':<20}")
    print(f"{'-'*60}")
    for key in ["success", "elapsed_s", "total_steps", "token_estimate"]:
        s = results.get("som", {}).get(key, "N/A")
        d = results.get("direct", {}).get(key, "N/A")
        print(f"{key:<20} {str(s):<20} {str(d):<20}")

    # 保存结果
    out_path = os.path.join(os.path.dirname(__file__), "..", "som_vs_direct_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"goal": GOAL, "results": results, "config": {"api_base": API_BASE, "model": MODEL}},
                  f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
