"""能力演示 · 一键运行。

自动完成：启动 WinPilot 服务（若未启动）→ 打开演示靶场 → 依次运行
第一部分（离线 ARP）与第二部分（遗留 DSL）→ 汇总结果 → 关闭靶场。

用法（在仓库根目录）：
    python scripts/能力演示-一键运行.py

结束后查看：output/能力演示/ 下的两个报告，以及
python/winpilot_home/eval_targets/能力演示.flow（可在 Electron Studio 打开）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / "python-runtime" / "python.exe"
PY_HOME = REPO / "python" / "winpilot_home"
TARGET = REPO / "python" / "winpilot_home" / "eval_targets" / "能力演示靶场.py"
PART1 = REPO / "scripts" / "能力演示-第一部分-离线ARP.py"
PART2 = REPO / "scripts" / "能力演示-第二部分-遗留DSL.py"
OUT_DIR = REPO / "output" / "能力演示"
ORACLE = OUT_DIR / "靶场状态.json"
TOKEN = "ecc-demo-token"
PORT = "8765"


def _ping() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=3) as r:
            return json.loads(r.read().decode("utf-8")).get("ok") is True
    except Exception:
        return False


def _token_ok() -> bool:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/v2/runs/current")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return e.code != 401
    except Exception:
        return False


def _ensure_server() -> subprocess.Popen | None:
    if _ping():
        if not _token_ok():
            raise RuntimeError(
                "检测到已有 WinPilot 服务在运行，但控制令牌不匹配。"
                "请先关闭它，或把环境变量 WINPILOT_CONTROL_TOKEN 设成与它一致。"
            )
        print("服务已在运行（令牌匹配）。")
        return None
    print("启动 WinPilot 服务 ...")
    env = dict(os.environ, WINPILOT_CONTROL_TOKEN=TOKEN)
    proc = subprocess.Popen([str(PY), "-u", "serve.py", PORT, "127.0.0.1"],
                            cwd=str(PY_HOME), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        if _ping():
            print("服务已就绪。")
            return proc
    raise RuntimeError("WinPilot 服务启动失败")


def _run(script: Path, env: dict) -> None:
    print(f"\n>>> 运行 {script.name}")
    subprocess.run([str(PY), str(script)], cwd=str(REPO), env=env, check=False)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ORACLE.unlink(missing_ok=True)

    server = _ensure_server()
    print("打开演示靶场 ...")
    target = subprocess.Popen([str(PY), str(TARGET), "--state-file", str(ORACLE), "--case-id", "演示"],
                              cwd=str(REPO))
    for _ in range(20):
        time.sleep(0.5)
        if ORACLE.exists():
            break
    time.sleep(1.5)

    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               WINPILOT_SERVE_PORT=PORT, WINPILOT_CONTROL_TOKEN=TOKEN)
    try:
        _run(PART1, env)
        _run(PART2, env)
    finally:
        target.terminate()
        if server is not None:
            server.terminate()

    print("\n=== 汇总 ===")
    parts = [("第一部分 · 离线 ARP（canonical）", OUT_DIR / "第一部分-离线ARP报告.json"),
             ("第二部分 · 遗留 DSL 引擎", OUT_DIR / "第二部分-遗留DSL报告.json")]
    matrix = ["# WinPilot 能力演示 · 端到端矩阵（本次运行实测）", "", "> 校验依据：靶场自己写出的 JSON（运行时之外），"+ "不信任运行时报的 done。", ""]
    for label, path in parts:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            checks = data.get("检查", [])
            ok = sum(1 for c in checks if c.get("通过"))
            print(f"  {label}: {ok}/{len(checks)} 通过")
            matrix += [f"## {label}", "", "| # | 检查项 | 独立校验依据 | 结果 |", "|---|---|---|---|"]
            for i, c in enumerate(checks, 1):
                matrix.append(f"| {i} | {c['项']} | {c['详情']} | {'通过' if c['通过'] else '未过'} |")
            matrix.append("")
        else:
            print(f"  {label}: 未生成报告")
    p1 = OUT_DIR / "第一部分-离线ARP报告.json"
    if p1.exists():
        mc = json.loads(p1.read_text(encoding="utf-8")).get("模型调用次数")
        matrix.insert(3, f"> 第一部分模型调用次数：{mc}（离线应为 0）")
    (OUT_DIR / "端到端矩阵.md").write_text("\n".join(matrix), encoding="utf-8")
    print(f"\n报告目录: {OUT_DIR}")
    print(f"端到端矩阵: {OUT_DIR / '端到端矩阵.md'}")
    print(f"流程文件: {REPO / 'python' / 'winpilot_home' / 'eval_targets' / '能力演示.flow'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
