"""Run one real WinPilot GUI task and write a compact evidence report.

This script assumes the WinPilot server is already running, usually through the
Electron frontend. It does not bypass the GUI path: it submits the same
``/api/chat`` request the frontend uses, waits for completion, reads the trace,
and writes a JSON report that points at screenshots and summarizes suspicious
done-gate / tool behavior.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_OUT_DIR = Path("python/winpilot_home/evals")
REPO_ROOT = Path(__file__).resolve().parents[1]
WINPILOT_PY = REPO_ROOT / "python" / "winpilot_home"
if str(WINPILOT_PY) not in sys.path:
    sys.path.insert(0, str(WINPILOT_PY))

SUITES: dict[str, list[dict[str, Any]]] = {
    "gui-smoke": [
        {
            "id": "calculator_sum",
            "task": "用计算器计算86和79的和，确认结果是165。必须通过GUI操作。",
            "timeout_s": 180,
        },
        {
            "id": "notepad_save",
            "task": (
                "打开记事本，输入两行文字：第一行 WinPilot E2E 记录，"
                "第二行 时间测试通过。然后通过GUI另存为桌面上的 "
                "WinPilotE2E_note.txt。必须通过GUI操作，最后确认文件已保存。"
            ),
            "timeout_s": 360,
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotE2E_note.txt\" -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null"
            ],
            "checks": [
                {
                    "name": "notepad_file_contains_payload",
                    "command": "if ((Test-Path -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotE2E_note.txt\") -and ((Get-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotE2E_note.txt\" -Raw -Encoding UTF8) -match 'WinPilot E2E 记录') -and ((Get-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotE2E_note.txt\" -Raw -Encoding UTF8) -match '时间测试通过')) { 'OK'; exit 0 } else { exit 1 }",
                }
            ],
        },
    ],
    "gui-manipulation": [
        {
            "id": "desktop_drag",
            "task": (
                "把桌面上的 WinPilotPerfDrag_Eval 文件夹图标通过鼠标拖动到桌面右上角附近。"
                "不要打开它，不要删除它，不要移动其他图标。如果当前窗口遮挡桌面，先显示桌面。"
                "必须通过GUI拖动操作完成。"
            ),
            "timeout_s": 240,
            "setup": [
                "$p = Join-Path $env:USERPROFILE 'Desktop\\WinPilotPerfDrag_Eval'; "
                "New-Item -ItemType Directory -Force -Path $p | Out-Null; "
                "Set-Content -LiteralPath (Join-Path $p 'marker.txt') -Value 'WinPilot drag eval' -Encoding UTF8"
            ],
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotPerfDrag_Eval\" -Recurse -Force -ErrorAction SilentlyContinue"
            ],
            "checks": [
                {
                    "name": "drag_folder_still_exists",
                    "command": "if (Test-Path -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotPerfDrag_Eval\") { 'OK'; exit 0 } else { exit 1 }",
                }
            ],
        },
        {
            "id": "paint_triangle",
            "task": "在当前画图画布空白处画一个小三角形，不用保存。必须通过GUI操作。如果画图没打开就先打开画图。",
            "timeout_s": 240,
        },
    ],
    "gui-target": [
        {
            "id": "target_coordinate_click",
            "task": (
                "打开 http://127.0.0.1:8877/gui_agent_target.html?v=coord2，"
                "点击页面上的“校准点”按钮，最后确认页面出现 CALIBRATION_OK。"
                "必须通过GUI真实鼠标点击，不要用脚本注入点击。"
            ),
            "timeout_s": 240,
            "setup": [
                "$port = 8877; "
                "$ok = $false; "
                "try { $ok = (Test-NetConnection 127.0.0.1 -Port $port -InformationLevel Quiet) } catch { $ok = $false }; "
                "if (-not $ok) { "
                "Start-Process -FilePath python -ArgumentList @('-m','http.server','8877','--bind','127.0.0.1') "
                "-WorkingDirectory 'C:\\Users\\zou\\Desktop\\claude\\python\\winpilot_home\\eval_targets' "
                "-WindowStyle Hidden; Start-Sleep -Seconds 2 }"
            ],
            "forbid_tools": ["run_shell", "run_detached", "write_file", "read_file"],
        },
        {
            "id": "target_transient_press",
            "task": (
                "Open http://127.0.0.1:8877/gui_agent_target.html?v=press-pulse. "
                "Use a real physical mouse press on the button labeled Press pulse, then use "
                "changed-only visual perception to confirm PRESS_PULSE_ACTIVE appeared during "
                "the press. Do not use UIA Invoke, JavaScript, DevTools, or shell inspection."
            ),
            "timeout_s": 240,
            "setup": [
                "$port = 8877; "
                "$ok = $false; "
                "try { $ok = (Test-NetConnection 127.0.0.1 -Port $port -InformationLevel Quiet) } catch { $ok = $false }; "
                "if (-not $ok) { "
                "Start-Process -FilePath python -ArgumentList @('-m','http.server','8877','--bind','127.0.0.1') "
                "-WorkingDirectory 'C:\\Users\\zou\\Desktop\\claude\\python\\winpilot_home\\eval_targets' "
                "-WindowStyle Hidden; Start-Sleep -Seconds 2 }"
            ],
            "forbid_tools": ["run_shell", "run_detached", "write_file", "read_file"],
            "trace_checks": [
                {
                    "name": "pressed_state_was_captured",
                    "event_type": "screen_delta",
                    "field": "transient_observed",
                    "equals": True,
                }
            ],
        },
    ],
    "gui-deep-10": [
        {
            "id": "deep_calc_chain",
            "task": "用计算器通过GUI计算 123 + 456 - 78 的结果，确认最终结果是501。必须通过GUI操作，不要用命令行计算。",
            "timeout_s": 240,
            "setup": ["taskkill /f /im CalculatorApp.exe 2>$null"],
            "cleanup": ["taskkill /f /im CalculatorApp.exe 2>$null"],
        },
        {
            "id": "deep_notepad_save_multiline",
            "task": "打开记事本，通过GUI输入两行文字：第一行 深度测试A，第二行 GUI保存成功。然后另存为桌面 WinPilotDeep_note1.txt，并确认保存成功。",
            "timeout_s": 360,
            "setup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_note1.txt\" -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null"
            ],
            "checks": [
                {
                    "name": "file_contains_multiline",
                    "command": "if ((Test-Path -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_note1.txt\") -and ((Get-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_note1.txt\" -Raw -Encoding UTF8) -match '深度测试A') -and ((Get-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_note1.txt\" -Raw -Encoding UTF8) -match 'GUI保存成功')) { 'OK'; exit 0 } else { exit 1 }",
                }
            ],
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_note1.txt\" -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null"
            ],
        },
        {
            "id": "deep_notepad_append_existing",
            "task": "桌面上已有 WinPilotDeep_append.txt。请通过GUI用记事本打开它，在末尾新增一行 追加行-通过GUI，然后保存关闭。不要用命令行改文件。",
            "timeout_s": 420,
            "setup": [
                "Set-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_append.txt\" -Value \"初始行\" -Encoding UTF8; taskkill /f /im notepad.exe 2>$null"
            ],
            "checks": [
                {
                    "name": "append_file_contains_new_line",
                    "command": "if ((Test-Path -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_append.txt\") -and ((Get-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_append.txt\" -Raw -Encoding UTF8) -match '初始行') -and ((Get-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_append.txt\" -Raw -Encoding UTF8) -match '追加行-通过GUI')) { 'OK'; exit 0 } else { exit 1 }",
                }
            ],
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_append.txt\" -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null"
            ],
        },
        {
            "id": "deep_notepad_clipboard_paste",
            "task": "打开记事本，通过GUI把文字 剪贴板粘贴验证-红蓝绿 输入进去，可以使用剪贴板粘贴，然后另存为桌面 WinPilotDeep_clipboard.txt。",
            "timeout_s": 360,
            "setup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_clipboard.txt\" -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null"
            ],
            "checks": [
                {
                    "name": "clipboard_file_contains_payload",
                    "command": "if ((Test-Path -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_clipboard.txt\") -and ((Get-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_clipboard.txt\" -Raw -Encoding UTF8) -match '剪贴板粘贴验证-红蓝绿')) { 'OK'; exit 0 } else { exit 1 }",
                }
            ],
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_clipboard.txt\" -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null"
            ],
        },
        {
            "id": "deep_desktop_drag_folder",
            "task": "把桌面上的 WinPilotDeepDrag 文件夹图标通过鼠标拖动到桌面右上角附近。不要打开它，不要删除它，不要移动其他图标。必须通过GUI拖动完成。",
            "timeout_s": 300,
            "setup": [
                "$p = Join-Path $env:USERPROFILE 'Desktop\\WinPilotDeepDrag'; New-Item -ItemType Directory -Force -Path $p | Out-Null; Set-Content -LiteralPath (Join-Path $p 'marker.txt') -Value 'deep drag' -Encoding UTF8"
            ],
            "checks": [
                {
                    "name": "drag_folder_still_exists",
                    "command": "if (Test-Path -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeepDrag\") { 'OK'; exit 0 } else { exit 1 }",
                }
            ],
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeepDrag\" -Recurse -Force -ErrorAction SilentlyContinue"
            ],
        },
        {
            "id": "deep_paint_triangle_no_save",
            "task": "打开画图，在空白画布中间通过GUI画一个明显的小三角形，不用保存。完成前确认画布上确实有可见图形。",
            "timeout_s": 300,
            "setup": ["taskkill /f /im mspaint.exe 2>$null"],
            "cleanup": ["taskkill /f /im mspaint.exe 2>$null"],
        },
        {
            "id": "deep_paint_save_png",
            "task": "打开画图，在画布上通过GUI画一个明显的矩形或三角形，然后通过GUI保存为桌面 WinPilotDeepPaint.png。必须确认文件已保存。",
            "timeout_s": 480,
            "setup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeepPaint.png\" -Force -ErrorAction SilentlyContinue; taskkill /f /im mspaint.exe 2>$null"
            ],
            "checks": [
                {
                    "name": "paint_png_exists",
                    "command": "if ((Test-Path -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeepPaint.png\") -and ((Get-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeepPaint.png\").Length -gt 1000)) { 'OK'; exit 0 } else { exit 1 }",
                }
            ],
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeepPaint.png\" -Force -ErrorAction SilentlyContinue; taskkill /f /im mspaint.exe 2>$null"
            ],
        },
        {
            "id": "deep_multi_window_calc_note",
            "task": "先打开计算器通过GUI计算 9 × 9 得到81，再打开记事本写入 9乘9等于81，并另存为桌面 WinPilotDeep_multi.txt。必须通过GUI完成主要操作。",
            "timeout_s": 540,
            "setup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_multi.txt\" -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null; taskkill /f /im CalculatorApp.exe 2>$null"
            ],
            "checks": [
                {
                    "name": "multi_file_contains_result",
                    "command": "if ((Test-Path -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_multi.txt\") -and ((Get-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_multi.txt\" -Raw -Encoding UTF8) -match '9乘9等于81')) { 'OK'; exit 0 } else { exit 1 }",
                }
            ],
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_multi.txt\" -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null; taskkill /f /im CalculatorApp.exe 2>$null"
            ],
        },
        {
            "id": "deep_long_conditional_note",
            "task": "完成一个稍复杂的GUI任务：如果计算器能打开，就用计算器算 24 × 7；如果结果是168，就打开记事本写入 条件分支通过：168，并保存到桌面 WinPilotDeep_conditional.txt；如果计算器打不开，则在记事本写入 计算器打不开。全程主要操作必须走GUI。",
            "timeout_s": 540,
            "max_steps": 70,
            "setup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_conditional.txt\" -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null; taskkill /f /im CalculatorApp.exe 2>$null"
            ],
            "checks": [
                {
                    "name": "conditional_file_contains_expected_branch",
                    "command": "if ((Test-Path -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_conditional.txt\") -and ((Get-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_conditional.txt\" -Raw -Encoding UTF8) -match '条件分支通过：168')) { 'OK'; exit 0 } else { exit 1 }",
                }
            ],
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeep_conditional.txt\" -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null; taskkill /f /im CalculatorApp.exe 2>$null"
            ],
        },
        {
            "id": "deep_notepad_save_in_folder",
            "task": "桌面上已有文件夹 WinPilotDeepFolder。请通过GUI打开记事本，输入 文件夹内保存成功，然后另存为 WinPilotDeepFolder\\inside.txt，并确认保存成功。",
            "timeout_s": 420,
            "setup": [
                "$p = Join-Path $env:USERPROFILE 'Desktop\\WinPilotDeepFolder'; New-Item -ItemType Directory -Force -Path $p | Out-Null; Remove-Item -LiteralPath (Join-Path $p 'inside.txt') -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null"
            ],
            "checks": [
                {
                    "name": "folder_file_contains_payload",
                    "command": "if ((Test-Path -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeepFolder\\inside.txt\") -and ((Get-Content -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeepFolder\\inside.txt\" -Raw -Encoding UTF8) -match '文件夹内保存成功')) { 'OK'; exit 0 } else { exit 1 }",
                }
            ],
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotDeepFolder\" -Recurse -Force -ErrorAction SilentlyContinue; taskkill /f /im notepad.exe 2>$null"
            ],
        },
    ],
}

DEFAULT_BUDGETS = {
    "duration_s": 240.0,
    "tool_count": 45,
    "observe_avg_ms": 2500.0,
    "cost_cny": 1.5,
}

DEFAULT_GUI_FORBIDDEN_TOOLS = {"run_detached", "write_file", "read_file"}
DEFAULT_GUI_FORBIDDEN_SHELL_PATTERNS = (
    "python ",
    "python.exe",
    "py ",
    "node ",
    "node.exe",
    ".ps1",
    "-executionpolicy",
    "pyautogui",
    "pywinauto",
    "subprocess",
    "sendkeys",
    "windows.automation",
    "wscript.shell",
)


def _request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else None


def _run_powershell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _run_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for check in checks:
        name = str(check.get("name") or "check")
        command = str(check.get("command") or "")
        proc = _run_powershell(command)
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        contains = check.get("contains")
        passed = proc.returncode == 0
        if contains is not None:
            passed = passed and str(contains) in stdout
        results.append({
            "name": name,
            "command": command,
            "passed": bool(passed),
            "exit_code": proc.returncode,
            "stdout": stdout[:1000],
            "stderr": stderr[:1000],
        })
    return results


def _run_trace_checks(events: list[dict[str, Any]], checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for check in checks:
        event_type = str(check.get("event_type") or "")
        field_path = [part for part in str(check.get("field") or "").split(".") if part]
        expected = check.get("equals", True)
        matches = []
        for event in events:
            if event_type and event.get("type") != event_type:
                continue
            value: Any = event
            for part in field_path:
                value = value.get(part) if isinstance(value, dict) else None
            if value == expected:
                matches.append(event)
        results.append({
            "name": str(check.get("name") or "trace_check"),
            "passed": bool(matches),
            "event_type": event_type,
            "field": ".".join(field_path),
            "expected": expected,
            "match_count": len(matches),
        })
    return results


def _gui_policy_applies(case: dict[str, Any]) -> bool:
    if "policy" in case:
        return bool(case["policy"])
    task = str(case.get("task") or "").casefold()
    compact = "".join(task.split())
    return (
        "gui" in task
        or "\u5fc5\u987b\u8d70gui" in compact
        or "\u901a\u8fc7gui" in compact
        or "\u4e0d\u8981\u7528\u547d\u4ee4\u884c" in compact
    )


def _run_policy_checks(events: list[dict[str, Any]], case: dict[str, Any]) -> dict[str, Any]:
    if not _gui_policy_applies(case):
        return {"passed": True, "violations": []}
    forbidden_tools = set(DEFAULT_GUI_FORBIDDEN_TOOLS)
    forbidden_tools.update(str(item) for item in case.get("forbid_tools", []))
    allowed_tools = set(str(item) for item in case.get("allow_tools", []))
    shell_patterns = list(DEFAULT_GUI_FORBIDDEN_SHELL_PATTERNS)
    shell_patterns.extend(str(item).casefold() for item in case.get("forbid_shell_patterns", []))
    violations: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "agent_tool":
            continue
        tool = str(event.get("tool") or "")
        args = event.get("args") if isinstance(event.get("args"), dict) else {}
        if tool in forbidden_tools and tool not in allowed_tools:
            violations.append({
                "tool": tool,
                "reason": "forbidden_tool_in_gui_policy",
                "args_preview": json.dumps(args, ensure_ascii=False)[:500],
            })
            continue
        if tool == "run_shell":
            command = str(args.get("command") or "").casefold()
            hit = next((pattern for pattern in shell_patterns if pattern in command), "")
            if hit:
                violations.append({
                    "tool": tool,
                    "reason": f"forbidden_shell_pattern:{hit}",
                    "command": str(args.get("command") or "")[:500],
                })
    return {"passed": not violations, "violations": violations[:20]}


def _load_summary_module():
    try:
        from winpilot.eval import summary as summary_mod
        return summary_mod
    except Exception:
        return None


def _summarize_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    summary_mod = _load_summary_module()
    if summary_mod is not None:
        return summary_mod.summarize_events(events)
    tool_events = [event for event in events if event.get("type") == "agent_tool"]
    screenshots = [
        event.get("screenshot") for event in events
        if event.get("screenshot")
    ]
    done_checks = [
        {
            "passed": event.get("passed"),
            "reason": event.get("reason", ""),
            "unverified": bool(event.get("unverified")),
        }
        for event in events
        if event.get("type") == "done_check"
    ]
    final = next(
        (event for event in reversed(events) if event.get("type") == "agent_done"),
        {},
    )
    tool_sequence = [
        {
            "tool": event.get("tool"),
            "elapsed_ms": event.get("elapsed_ms"),
            "terminal": event.get("terminal"),
            "result_preview": event.get("result_preview", ""),
            "screenshot": event.get("screenshot"),
        }
        for event in tool_events
    ]
    return {
        "status": final.get("status", ""),
        "result": final.get("result", ""),
        "tool_count": len(tool_events),
        "tool_sequence": tool_sequence,
        "screenshots": screenshots,
        "done_checks": done_checks,
        "rejected_done_count": sum(1 for item in done_checks if item.get("passed") is False),
    }


def _budget_result(report: dict[str, Any], budgets: dict[str, float]) -> dict[str, Any]:
    summary = report.get("summary") or report.get("trace_summary") or {}
    usage = report.get("usage") or report.get("status_endpoint", {}).get("usage") or {}
    observe = (summary.get("tool_latency") or {}).get("observe") or {}
    checks = {
        "duration_s": float(report.get("duration_s") or 0) <= float(budgets["duration_s"]),
        "tool_count": int(report.get("tool_count") or 0) <= int(budgets["tool_count"]),
        "observe_avg_ms": float(observe.get("avg_ms") or 0) <= float(budgets["observe_avg_ms"]),
        "cost_cny": float(usage.get("cost_cny") or 0) <= float(budgets["cost_cny"]),
    }
    return {"passed": all(checks.values()), "checks": checks, "budgets": budgets}


def run_eval(
    base_url: str,
    task: str,
    out_dir: Path,
    timeout_s: float,
    case_id: str = "",
    budgets: dict[str, float] | None = None,
    max_steps: int | None = None,
) -> Path:
    started_at = time.time()
    payload: dict[str, Any] = {"task": task}
    if max_steps:
        payload["max_steps"] = int(max_steps)
    started = _request_json("POST", f"{base_url}/api/chat", payload)
    run_id = str(started["run_id"])
    deadline = time.time() + timeout_s
    last_status: dict[str, Any] = {}
    timed_out = False
    while time.time() < deadline:
        last_status = _request_json("GET", f"{base_url}/api/status")
        if not last_status.get("running"):
            break
        time.sleep(1.0)
    else:
        _request_json("POST", f"{base_url}/api/stop")
        timed_out = True
        time.sleep(0.5)
        last_status = _request_json("GET", f"{base_url}/api/status")

    time.sleep(0.5)
    events = _request_json("GET", f"{base_url}/api/trace/{run_id}")
    if not isinstance(events, list):
        raise RuntimeError(f"Trace response for {run_id} was not a list")
    summary = _summarize_trace(events)
    if timed_out and not summary.get("status"):
        summary["status"] = "timeout"
    duration_s = round(time.time() - started_at, 2)
    report = {
        "run_id": run_id,
        "case_id": case_id,
        "task": task,
        "base_url": base_url,
        "timed_out": timed_out,
        "status": summary.get("status", ""),
        "result": summary.get("result", ""),
        "duration_s": duration_s,
        "tool_count": summary.get("tool_count", 0),
        "done_checks": summary.get("done_checks", []),
        "rejected_done_count": summary.get("rejected_done_count", 0),
        "status_endpoint": last_status,
        "trace_file": f"python/winpilot_home/traces/{run_id}.jsonl",
        "screenshot_dir": f"python/winpilot_home/traces/{run_id}/",
        "summary": summary,
    }
    if budgets:
        report["performance_budget"] = _budget_result(report, budgets)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.eval.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_suite(base_url: str, suite_name: str, out_dir: Path, timeout_s: float,
              budgets: dict[str, float], case_ids: set[str] | None = None) -> Path:
    cases = SUITES.get(suite_name)
    if not cases:
        raise SystemExit(f"Unknown suite {suite_name!r}. Available: {', '.join(sorted(SUITES))}")
    if case_ids:
        cases = [case for case in cases if str(case.get("id") or "") in case_ids]
        if not cases:
            raise SystemExit(f"No cases matched: {', '.join(sorted(case_ids))}")
    reports: list[dict[str, Any]] = []
    paths: list[str] = []
    for case in cases:
        for command in case.get("setup", []):
            _run_powershell(command)
        try:
            path = run_eval(
                base_url,
                str(case["task"]),
                out_dir,
                float(case.get("timeout_s") or timeout_s),
                case_id=str(case.get("id") or ""),
                budgets=budgets,
                max_steps=int(case.get("max_steps") or 0) or None,
            )
            report = json.loads(path.read_text(encoding="utf-8"))
            trace_path = REPO_ROOT / str(report.get("trace_file", ""))
            events = []
            if trace_path.exists():
                events = [
                    json.loads(line)
                    for line in trace_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            checks = _run_checks(list(case.get("checks", [])))
            trace_checks = _run_trace_checks(events, list(case.get("trace_checks", [])))
            all_checks = [*checks, *trace_checks]
            if all_checks:
                report["ground_truth_checks"] = all_checks
                report["ground_truth_passed"] = all(item["passed"] for item in all_checks)
            policy = _run_policy_checks(events, case)
            report["policy_checks"] = policy
            report["policy_passed"] = bool(policy.get("passed"))
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            paths.append(str(path))
            reports.append(report)
        finally:
            for command in case.get("cleanup", []):
                _run_powershell(command)

    summary_mod = _load_summary_module()
    aggregate = summary_mod.aggregate_reports(reports) if summary_mod else {
        "total": len(reports),
        "reports": reports,
    }
    aggregate["suite"] = suite_name
    aggregate["report_files"] = paths
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suite_path = out_dir / f"{stamp}-{suite_name}.suite.eval.json"
    suite_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    return suite_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real WinPilot GUI eval task.")
    parser.add_argument("task", nargs="?", help="Task text to submit through /api/chat.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"WinPilot server URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                        help=f"Report output directory. Default: {DEFAULT_OUT_DIR}")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="Maximum seconds to wait before stopping the run.")
    parser.add_argument("--suite", choices=sorted(SUITES),
                        help="Run a built-in GUI eval suite through /api/chat.")
    parser.add_argument("--case", action="append", default=[],
                        help="Run only a case id from the selected suite. Can be repeated.")
    parser.add_argument("--list-suites", action="store_true",
                        help="List built-in suites and exit.")
    parser.add_argument("--budget-duration", type=float, default=DEFAULT_BUDGETS["duration_s"])
    parser.add_argument("--budget-tools", type=int, default=DEFAULT_BUDGETS["tool_count"])
    parser.add_argument("--budget-observe-avg-ms", type=float, default=DEFAULT_BUDGETS["observe_avg_ms"])
    parser.add_argument("--budget-cost", type=float, default=DEFAULT_BUDGETS["cost_cny"])
    args = parser.parse_args()
    if args.list_suites:
        for name, cases in SUITES.items():
            print(f"{name}: {len(cases)} cases")
        return 0
    budgets = {
        "duration_s": args.budget_duration,
        "tool_count": args.budget_tools,
        "observe_avg_ms": args.budget_observe_avg_ms,
        "cost_cny": args.budget_cost,
    }
    try:
        if args.suite:
            path = run_suite(args.base_url.rstrip("/"), args.suite, args.out_dir,
                             args.timeout, budgets, case_ids=set(args.case) or None)
        else:
            if not args.task:
                raise SystemExit("task is required unless --suite is used")
            path = run_eval(args.base_url.rstrip("/"), args.task, args.out_dir,
                            args.timeout, budgets=budgets)
    except urllib.error.URLError as exc:
        raise SystemExit(f"WinPilot server request failed: {exc}") from exc
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
