"""
Claude Code 委托功能
打开新终端窗口，启动另一个 Claude Code 实例进行询问/通信/并行任务
"""
import os
import subprocess
import sys
import tempfile
import time

from features.registry import feature, P, TC, FeatureCategory as F


def _detect_claude() -> str:
    """探测 claude 命令路径"""
    if sys.platform == "win32":
        for cmd in ["where", "npx.cmd"]:
            try:
                r = subprocess.run([cmd, "claude", "--version"],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    return "claude" if cmd == "where" else "npx.cmd claude"
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return "npx.cmd claude"
    else:
        for cmd in ["which", "npx"]:
            try:
                r = subprocess.run([cmd, "claude", "--version"],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    return "claude" if cmd == "which" else "npx claude"
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return "npx claude"


@feature(
    name="claude_code_delegate",
    display_name="Claude Code 委托",
    description="【高级·自指】打开新终端窗口启动另一个 Claude Code 实例，向其发送任务/问题。"
                "适用于 Claude Code 自我复制、并行处理子任务、跨实例通信等高级操作。"
                "新窗口独立运行，原 Claude Code 可继续工作",
    category=F.SYSTEM,
    params=[
        P("prompt", "str", "要发送给新 Claude Code 实例的任务描述或问题", example="分析当前项目中的错误日志"),
        P("model", "str", "指定模型 (opus/sonnet/haiku)，留空使用默认", required=False, default=""),
        P("wait", "boolean", "是否等待新实例执行完毕后返回结果（阻塞直到完成）",
          required=False, default=False),
        P("title", "str", "新终端窗口标题（仅部分终端支持）", required=False, default="Claude Code"),
    ],
    returns="dict{success, pid, message}",
    test_cases=[],
)
def claude_code_delegate(
    prompt: str = "",
    model: str = "",
    wait: bool = False,
    title: str = "Claude Code",
) -> dict:
    if not prompt.strip():
        return {"success": False, "error": "prompt 不能为空"}

    claude = _detect_claude()
    model_flag = f" --model {model}" if model else ""

    # Write prompt to temp file to avoid shell escaping issues
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".prompt.txt", delete=False, encoding="utf-8"
    )
    tmp.write(prompt)
    tmp.close()

    try:
        if sys.platform == "win32":
            safe_path = tmp.name.replace("'", "''")
            # type <file> | claude  在新控制台窗口中运行
            shell_cmd = f'type "{safe_path}" | {claude}{model_flag}'
            proc = subprocess.Popen(
                ["cmd", "/k", shell_cmd],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            msg = f"已在新终端启动 Claude Code (PID={proc.pid})"
            if wait:
                proc.wait()
                msg += "，执行完毕"
            return {"success": True, "pid": proc.pid, "message": msg}

        elif sys.platform == "darwin":
            # macOS: osascript 打开新 Terminal 窗口
            escaped = prompt.replace("\\", "\\\\").replace('"', '\\"').replace("'", "'\\''")
            ascript = (
                f'tell app "Terminal" to activate\n'
                f'tell app "Terminal" to do script '
                f'"echo \'{escaped}\' | {claude}{model_flag}; exit"\n'
            )
            proc = subprocess.Popen(["osascript", "-e", ascript])
            return {"success": True, "pid": proc.pid, "message": "已在新 Terminal 窗口启动 Claude Code"}

        else:
            # Linux: 写临时 shell 脚本 → 启动终端
            sh = tempfile.NamedTemporaryFile(
                mode="w", suffix=".sh", delete=False, encoding="utf-8"
            )
            escaped = prompt.replace("'", "'\\''")
            sh.write(f'#!/bin/sh\necho \'{escaped}\' | {claude}{model_flag}\nexec $SHELL\n')
            sh.close()
            os.chmod(sh.name, 0o755)

            for term in ["x-terminal-emulator", "gnome-terminal", "xterm", "konsole"]:
                try:
                    proc = subprocess.Popen([term, "-e", sh.name])
                    return {"success": True, "pid": proc.pid, "message": f"已在新 {term} 窗口启动 Claude Code"}
                except FileNotFoundError:
                    continue
            return {"success": False, "error": "未找到可用的终端模拟器"}

    except Exception as e:
        return {"success": False, "error": str(e)}
