"""Ad-hoc E2E harness: run one WinPilot task on the REAL desktop, stream steps,
and capture real screenshots for visual verification. Not a pytest test."""
import sys
import time
import threading
import queue
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent / "python" / "winpilot_home"))

from winpilot.utils.log import BUS  # noqa: E402
from winpilot.utils.screenshot import capture  # noqa: E402
from winpilot.agent.loop import AgentRunner  # noqa: E402

OUT = Path(__file__).resolve().parent / "_e2e_shots"
OUT.mkdir(exist_ok=True)


def shot(tag: str) -> str:
    p = OUT / f"{tag}.png"
    cv2.imwrite(str(p), capture())
    return str(p)


def _drain(q: "queue.Queue", stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            kw = q.get(timeout=0.5)
        except queue.Empty:
            continue
        topic = kw.get("type")
        if topic == "agent_tool_start":
            print(f"[TOOL>] {kw.get('tool')} {str(kw.get('args'))[:110]}", flush=True)
        elif topic == "agent_tool":
            print(f"[TOOL=] {kw.get('tool')} -> {str(kw.get('result_preview'))[:110]}", flush=True)
        elif topic == "agent_step":
            r = (kw.get("reasoning") or "")[:160]
            if r:
                print(f"[THINK] {r}", flush=True)
        elif topic == "perception":
            dims = kw.get('dims') or {}
            dim_summary = " ".join(f"{k}:{v.get('count',0)}" + (f"[{v.get('skipped','')}]" if v.get('skipped') else "") for k, v in dims.items())
            print(f"[PERCEIVE] {kw.get('title')!r} {dim_summary}", flush=True)
        elif topic == "done_check":
            print(f"[DONE-CHECK] passed={kw.get('passed')} {kw.get('reason')}", flush=True)
        elif topic == "agent_reflect":
            print(f"[REFLECT] step={kw.get('step')} consecutive_unverified={kw.get('consecutive_unverified')}", flush=True)
        elif topic == "chat" and kw.get("role") == "assistant":
            print(f"[REPLY] {str(kw.get('content'))[:200]}", flush=True)


def main() -> None:
    task = sys.argv[1] if len(sys.argv) > 1 else "打开计算器"
    tag = sys.argv[2] if len(sys.argv) > 2 else "run"

    q = BUS.subscribe()
    stop = threading.Event()
    reader = threading.Thread(target=_drain, args=(q, stop), daemon=True)
    reader.start()

    print(f"=== TASK: {task} ===", flush=True)
    print("before:", shot(f"{tag}_before"), flush=True)

    runner = AgentRunner(task)

    def _run_with_com():
        import pythoncom
        pythoncom.CoInitialize()  # so UIA works in this worker thread (prod parity)
        try:
            runner.run()
        finally:
            pythoncom.CoUninitialize()

    t = threading.Thread(target=_run_with_com, daemon=True)
    t.start()
    t.join(timeout=600)

    time.sleep(1.0)
    stop.set()
    print("after:", shot(f"{tag}_after"), flush=True)
    print(f"=== STATUS: {runner.status} ===", flush=True)
    print(f"=== RESULT: {runner.result_text[:300]} ===", flush=True)


if __name__ == "__main__":
    main()
