"""
Agent 实测脚本 — 记录完整执行过程用于分析改进。
用法: python _agent_test_runner.py [task_index?]
"""
import json, os, sys, time, threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
os.environ["PYTHONIOENCODING"] = "utf-8"
# Force UTF-8 for stdout/stderr to prevent GBK encoding errors
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Load .env ──
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(_env_file):
    with open(_env_file, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line.startswith("OPENAI_API_KEY="):
                os.environ.setdefault("OPENAI_API_KEY", _line.split("=", 1)[1])
            elif _line.startswith("OPENAI_API_BASE="):
                os.environ.setdefault("OPENAI_API_BASE", _line.split("=", 1)[1])
            elif _line.startswith("VLM_MODEL="):
                os.environ.setdefault("VLM_MODEL", _line.split("=", 1)[1])

# ── 测试任务列表 ──
TASKS = [
    {
        "id": 1,
        "goal": "打开记事本，输入 'Hello World'，然后保存到桌面为 hello.txt",
        "desc": "基本操作链路：启动应用 → 输入文字 → 保存文件",
        "config": {
            "model": "mimo-v2-omni",
            "multimodal": "true",
            "agent_config": {
                "llm_protocol": "text",
                "planning": "per_step",
                "vision_mode": "direct",
                "screen_context": "minimal",
                "on_error": "retry",
                "max_steps": 15,
                "temperature": 0.1,
            }
        }
    },
    {
        "id": 2,
        "goal": "打开计算器并计算 7+3 等于多少",
        "desc": "GUI 交互：启动 → 点击按钮序列 → 读取结果",
        "config": {
            "model": "mimo-v2-omni",
            "multimodal": "true",
            "agent_config": {
                "llm_protocol": "text",
                "planning": "per_step",
                "vision_mode": "direct",
                "screen_context": "minimal",
                "on_error": "retry",
                "max_steps": 20,
                "temperature": 0.1,
            }
        }
    },
    {
        "id": 3,
        "goal": "在桌面创建文件 test.txt，内容为 'Hello from Agent'",
        "desc": "直接路径（要结果）：用 file_ops 而不是操作程序",
        "config": {
            "model": "mimo-v2-omni",
            "multimodal": "true",
            "agent_config": {
                "llm_protocol": "text",
                "planning": "per_step",
                "vision_mode": "direct",
                "screen_context": "minimal",
                "on_error": "retry",
                "max_steps": 10,
                "temperature": 0.1,
            }
        }
    },
]

# ── 日志收集器 ──
events_log = []
def event_collector(event_dict):
    events_log.append(event_dict.copy())
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    et = event_dict.get("type", "")
    msg = event_dict.get("message", event_dict.get("status", ""))
    if et == "step_complete":
        step = event_dict.get("step_num", 0)
        tool = event_dict.get("tool", "?")
        success = event_dict.get("result", {}).get("success", False)
        err = event_dict.get("result", {}).get("error", "")
        status = "✓" if success else "✗"
        print(f"  [{ts}] STEP {step}: {tool} {status} {err[:100] if err else ''}")
    elif et == "log":
        print(f"  [{ts}] LOG [{event_dict.get('level','info')}]: {msg[:150]}")
    elif et == "agent_done":
        print(f"  [{ts}] DONE: status={event_dict.get('status')}, "
              f"steps={event_dict.get('total_steps')}, "
              f"reason={event_dict.get('fail_reason','')[:200]}")
    elif et == "agent_reflection":
        print(f"  [{ts}] REFLECT step {event_dict.get('step_num')}: {event_dict.get('insight','')[:200]}")
    elif et == "agent_learned":
        print(f"  [{ts}] LEARNED: {event_dict.get('rules_count',0)} rules, "
              f"{event_dict.get('prefs_count',0)} prefs")
    else:
        print(f"  [{ts}] {et}: {str(msg)[:120]}")


def run_task(task):
    print(f"\n{'='*70}")
    print(f"任务 #{task['id']}: {task['goal']}")
    print(f"说明: {task['desc']}")
    print(f"{'='*70}")

    from agent.agent import LingdongAgent
    from agent.config import AgentConfig

    cfg_dict = task["config"].get("agent_config", {})
    agent_cfg = AgentConfig.from_dict(cfg_dict)

    stop_event = threading.Event()
    agent = LingdongAgent(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        api_base=os.environ.get("OPENAI_API_BASE", "https://api.xiaomimimo.com/v1"),
        model=task["config"].get("model", "mimo-v2-omni"),
        event_callback=event_collector,
        stop_event=stop_event,
        multimodal=task["config"].get("multimodal") == "true",
    )

    start = time.time()
    try:
        result = agent.run(task["goal"], agent_config=agent_cfg)
    except Exception as e:
        import traceback
        result = {"success": False, "error": str(e), "traceback": traceback.format_exc()}

    elapsed = time.time() - start

    print(f"\n--- 结果 (耗时 {elapsed:.1f}s) ---")
    print(f"  success: {result.get('success')}")
    print(f"  total_steps: {result.get('total_steps', 0)}")
    print(f"  summary: {result.get('summary', '')[:300]}")
    print(f"  error: {result.get('error', '')[:300]}")

    # 保存详细日志
    record = {
        "task": task,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "result": {k: v for k, v in result.items() if k != "steps"},
        "events": [
            {k: v for k, v in e.items() if k != "result" or e["type"] != "step_complete"}
            for e in events_log
        ],
    }
    # 包含步骤详情
    steps_data = result.get("steps", [])
    record["steps_detail"] = [
        {
            "step_num": s.get("step_num"),
            "tool": s.get("tool"),
            "thought": s.get("thought", "")[:500],
            "params": s.get("parameters", s.get("args", {})),
            "success": s.get("result", {}).get("success", False),
            "error": s.get("result", {}).get("error", "")[:300],
            "llm_response": s.get("llm_response", "")[:500],
        }
        for s in (steps_data or [])
    ]
    record["steps_detail_len"] = len(steps_data or [])

    os.makedirs("_agent_test_records", exist_ok=True)
    fname = f"_agent_test_records/task{task['id']}_{datetime.now().strftime('%H%M%S')}.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, default=str)
    print(f"  详情已保存: {fname}")
    print()

    return result

if __name__ == "__main__":
    import importlib
    # 先加载 features
    import features._all  # noqa
    from features.registry import registry
    registry.all()

    task_idx_str = sys.argv[1] if len(sys.argv) > 1 else ""
    if task_idx_str:
        task = TASKS[int(task_idx_str) - 1]
        run_task(task)
    else:
        for task in TASKS:
            events_log.clear()
            run_task(task)
