"""Shell execution: run cmd/PowerShell commands and capture their output.

The agent's ``launch`` tool is fire-and-forget (Popen) and returns no output —
useless for "list files / read result / query system" work. ``run`` here uses
subprocess.run with full stdout/stderr/exit-code capture, a hard timeout, and
UTF-8 decoding so the agent can actually see what a command produced.

Gated by ``config.agent.shell_enabled``: turning it off forces pure-GUI mode
(for validating that GUI-only paths work without any shell shortcuts).
"""
from __future__ import annotations

import subprocess
import tempfile
import time
import uuid

from ..config import CONFIG, SCRIPTS_DIR
from ..utils.log import BUS, logger
from . import safety

_DEFAULT_TIMEOUT_S = 30
_MAX_OUTPUT_CHARS = 6000  # what we hand back to the LLM (full text still logged)

# Execution/result semantics adapted from Pi's MIT-licensed bash tool at
# packages/coding-agent/src/core/tools/bash.ts, bash-executor.ts and
# truncate.ts (upstream commit f8c71c6a0693bc7f71f84e92783315d6a725a721).
# This is the Python/Windows port used by WinPilot; Pi is not embedded as a
# second agent runtime.
_PI_MAX_OUTPUT_LINES = 2000
_PI_MAX_OUTPUT_BYTES = 50 * 1024


def shell_enabled() -> bool:
    return bool(CONFIG.get("agent", "shell_enabled", default=True))


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2:]
    return f"{head}\n...（输出已截断 {len(text) - limit} 字符）...\n{tail}"


def _truncate_tail(
    text: str,
    *,
    max_lines: int = _PI_MAX_OUTPUT_LINES,
    max_bytes: int = _PI_MAX_OUTPUT_BYTES,
) -> tuple[str, dict]:
    """Keep Pi-style tail output and describe exactly what was omitted."""

    text = text or ""
    total_lines = len(text.splitlines())
    total_bytes = len(text.encode("utf-8"))
    output = text
    truncated_by: list[str] = []

    lines = output.splitlines(keepends=True)
    if len(lines) > max_lines:
        output = "".join(lines[-max_lines:])
        truncated_by.append("lines")

    encoded = output.encode("utf-8")
    if len(encoded) > max_bytes:
        # Decode with ignore so a byte cut through a multi-byte character
        # never leaks replacement glyphs into the model context.
        output = encoded[-max_bytes:].decode("utf-8", errors="ignore")
        truncated_by.append("bytes")

    return output, {
        "truncated": bool(truncated_by),
        "truncated_by": "+".join(truncated_by),
        "total_lines": total_lines,
        "total_bytes": total_bytes,
        "shown_lines": len(output.splitlines()),
        "shown_bytes": len(output.encode("utf-8")),
    }


def _save_full_output(text: str) -> str | None:
    """Save truncated output like Pi so the model can inspect it on demand."""

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="winpilot-pi-shell-",
            suffix=".log",
            delete=False,
        ) as handle:
            handle.write(text)
            return handle.name
    except OSError as exc:
        logger.debug("failed to save full shell output: %s", exc)
        return None


def _shape_result(
    *,
    stdout: str,
    stderr: str,
    ok: bool,
    exit_code: int | None,
    error: str = "",
    timeout: bool = False,
    stopped: bool = False,
) -> dict:
    """Return bounded tail output plus an inspectable full-output path."""

    combined = stdout
    if stderr:
        combined = f"{combined}\n{stderr}" if combined else stderr
    output, output_meta = _truncate_tail(
        combined, max_bytes=_MAX_OUTPUT_CHARS
    )
    stdout_tail, _ = _truncate_tail(stdout, max_bytes=_MAX_OUTPUT_CHARS)
    stderr_tail, _ = _truncate_tail(stderr, max_bytes=_MAX_OUTPUT_CHARS)
    full_output_path = (
        _save_full_output(combined) if output_meta["truncated"] else None
    )
    result = {
        "ok": ok,
        "exit_code": exit_code,
        "stdout": stdout_tail,
        "stderr": stderr_tail,
        "output": output or "(no output)",
        "output_meta": output_meta,
        "foreground_window": _foreground_window_fact(),
    }
    if full_output_path:
        result["full_output_path"] = full_output_path
    if error:
        result["error"] = error
    if timeout:
        result["timeout"] = True
    if stopped:
        result["stopped"] = True
    if not ok:
        if timeout:
            result["next_step"] = (
                "inspect partial output, split the command, or increase timeout"
            )
        elif stopped:
            result["next_step"] = "do not retry unless the run continues"
        elif exit_code not in (None, 0):
            result["next_step"] = (
                "use the output to correct the command or choose another tool"
            )
        else:
            result["next_step"] = (
                "change the command or choose another tool"
            )
    return result


# PowerShell 5.1 pipes output in the ANSI codepage (GBK on Chinese systems);
# force UTF-8 so Chinese output isn't mojibake (the snake-game run produced
# "�ļ��Ѵ���" which hid a real failure from the model).
_PS_UTF8_PREFIX = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "


def _decode(data: bytes, prefer: tuple[str, ...]) -> str:
    """Try encodings in order, strict first; last resort replaces."""
    for enc in prefer:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode(prefer[0], errors="replace")


def _kill_process_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as exc:
        logger.debug("failed to kill shell process tree pid=%s: %s", pid, exc)


def _run_legacy_subprocess(command: str, shell: str = "powershell",
                           timeout_s: float = _DEFAULT_TIMEOUT_S, cwd: str | None = None) -> dict:
    """Run a command, capture output. Never raises — errors come back in the dict.

    shell: "powershell" (default) or "cmd".
    """
    if not shell_enabled():
        return {"ok": False, "error": "shell 已被禁用（强制 GUI 模式）。请改用界面操作工具。"}
    if not command or not command.strip():
        return {"ok": False, "error": "命令为空"}
    blocked = safety.guard(command)
    if blocked is not None:
        BUS.publish("action", action="shell_blocked", command=command[:200],
                    reason=blocked.get("error") or blocked.get("note"))
        return blocked

    if shell == "cmd":
        argv = ["cmd", "/c", command]
        prefer = ("gbk", "utf-8")  # cmd writes the ANSI codepage
    else:
        # -NoProfile keeps startup fast and deterministic; bypass execution policy
        # so ad-hoc scripts run without per-machine policy friction.
        argv = ["powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command",
                _PS_UTF8_PREFIX + command]
        prefer = ("utf-8", "gbk")

    BUS.publish("action", action="shell", shell=shell, command=command[:400])
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stdout_b, stderr_b = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        logger.warning("shell 命令超时(%ss): %s", timeout_s, command[:120])
        return {"ok": False, "error": f"命令超时（{timeout_s}s）", "timeout": True}
    except Exception as exc:
        logger.warning("shell 执行异常: %s", exc)
        return {"ok": False, "error": str(exc)}

    stdout = _truncate(_decode(proc.stdout or b"", prefer))
    stderr = _truncate(_decode(proc.stderr or b"", prefer))
    ok = proc.returncode == 0
    BUS.publish("action", action="shell_result", exit_code=proc.returncode,
                ok=ok, output_preview=(stdout or stderr)[:300])
    return {
        "ok": ok,
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def run(command: str, shell: str = "powershell",
        timeout_s: float = _DEFAULT_TIMEOUT_S, cwd: str | None = None,
        stop_event=None) -> dict:
    """Run a command, capture output, and kill the whole process tree on timeout."""
    operation_id = uuid.uuid4().hex
    if not shell_enabled():
        BUS.publish(
            "action", action="shell_blocked", command=command[:200],
            reason="shell is disabled", operation_id=operation_id,
        )
        return {"ok": False, "error": "shell is disabled"}
    if not command or not command.strip():
        BUS.publish(
            "action", action="shell_blocked", command="",
            reason="empty command", operation_id=operation_id,
        )
        return {"ok": False, "error": "empty command"}
    blocked = safety.guard(command)
    if blocked is not None:
        BUS.publish("action", action="shell_blocked", command=command[:200],
                    reason=blocked.get("error") or blocked.get("note"),
                    operation_id=operation_id)
        return blocked

    if shell == "cmd":
        argv = ["cmd", "/c", command]
        prefer = ("gbk", "utf-8")
    else:
        argv = ["powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command",
                _PS_UTF8_PREFIX + command]
        prefer = ("utf-8", "gbk")

    BUS.publish(
        "action", action="shell", shell=shell, command=command[:400],
        operation_id=operation_id,
    )
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if stop_event is None:
            stdout_b, stderr_b = proc.communicate(timeout=timeout_s)
        else:
            deadline = time.monotonic() + float(timeout_s)
            while True:
                if stop_event.is_set():
                    _kill_process_tree(proc.pid)
                    try:
                        stdout_b, stderr_b = proc.communicate(timeout=2)
                    except Exception:
                        stdout_b, stderr_b = b"", b""
                    BUS.publish(
                        "action", action="shell_result", exit_code=None,
                        ok=False, output_preview="stopped",
                        operation_id=operation_id,
                    )
                    return _shape_result(
                        stdout=_decode(stdout_b or b"", prefer),
                        stderr=_decode(stderr_b or b"", prefer),
                        ok=False,
                        exit_code=None,
                        error="command stopped",
                        stopped=True,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout_s)
                try:
                    stdout_b, stderr_b = proc.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
    except subprocess.TimeoutExpired:
        if proc is not None:
            _kill_process_tree(proc.pid)
            try:
                stdout_b, stderr_b = proc.communicate(timeout=2)
            except Exception:
                stdout_b, stderr_b = b"", b""
        logger.warning("shell command timed out (%ss): %s", timeout_s, command[:120])
        BUS.publish(
            "action", action="shell_result", exit_code=None,
            ok=False, output_preview=f"timeout after {timeout_s}s",
            operation_id=operation_id,
        )
        return _shape_result(
            stdout=_decode(stdout_b or b"", prefer),
            stderr=_decode(stderr_b or b"", prefer),
            ok=False,
            exit_code=None,
            error=f"command timed out ({timeout_s}s)",
            timeout=True,
        )
    except Exception as exc:
        logger.warning("shell execution failed: %s", exc)
        BUS.publish(
            "action", action="shell_result", exit_code=None,
            ok=False, output_preview=str(exc)[:300],
            operation_id=operation_id,
        )
        return _shape_result(
            stdout="",
            stderr="",
            ok=False,
            exit_code=None,
            error=str(exc),
        )

    stdout = _decode(stdout_b or b"", prefer)
    stderr = _decode(stderr_b or b"", prefer)
    ok = proc.returncode == 0
    BUS.publish(
        "action", action="shell_result", exit_code=proc.returncode,
        ok=ok, output_preview=(stdout or stderr)[:300],
        operation_id=operation_id,
    )
    return _shape_result(
        stdout=stdout,
        stderr=stderr,
        ok=ok,
        exit_code=proc.returncode,
        error=("" if ok else f"command exited with code {proc.returncode}"),
    )


def _foreground_window_fact() -> dict[str, Any]:
    """Return the current foreground window as a bounded fact.

    Shell commands that start GUI apps (``Start-Process notepad.exe``) or
    inject keys (``SendKeys``) act on whatever window owns the foreground,
    which the model cannot see during the initial text turn.  Reporting the
    affected window keeps the model from mistaking a window it never looked
    at for the task target.
    """
    try:
        import ctypes

        hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        if not hwnd:
            return {"hwnd": 0, "available": False}
        title = ""
        class_name = ""
        try:
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(max(length, 0) + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
            title = buffer.value[:200]
        except Exception:
            pass
        try:
            class_buffer = ctypes.create_unicode_buffer(64)
            ctypes.windll.user32.GetClassNameW(hwnd, class_buffer, 64)
            class_name = class_buffer.value
        except Exception:
            pass
        process_id = ctypes.c_ulong(0)
        try:
            ctypes.windll.user32.GetWindowThreadProcessId(
                hwnd, ctypes.byref(process_id)
            )
        except Exception:
            pass
        return {
            "hwnd": int(hwnd),
            "title": title,
            "class_name": class_name,
            "process_id": int(process_id.value or 0),
            "available": True,
        }
    except Exception:
        return {"hwnd": 0, "available": False}


def _sweep_old(directory, max_age_s: int = 7 * 24 * 3600, keep: int = 200) -> None:
    """Drop detached .ps1 files older than max_age (and cap total count) so the
    directory can't grow unbounded. Best-effort; never raises."""
    try:
        files = sorted(directory.glob("*.ps1"), key=lambda p: p.stat().st_mtime)
        now = time.time()
        stale = [p for p in files if now - p.stat().st_mtime > max_age_s]
        excess = files[:-keep] if len(files) > keep else []
        for p in set(stale) | set(excess):
            p.unlink(missing_ok=True)
    except OSError:
        pass


def run_detached(script: str, name: str = "task") -> dict:
    """Launch a PowerShell script as a DETACHED, host-independent process.

    This is the reliable primitive for delayed / scheduled / long-running
    background work — the one thing ``run`` cannot do (its process dies the
    moment the command returns, taking Start-Job with it).

    The script is written to a real ``.ps1`` file and launched via ``-File``,
    which sidesteps the quoting/EncodedCommand hell that broke every hand-rolled
    attempt. The child is started with DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    so it outlives the WinPilot server. Returns the PID so the agent can verify
    the schedule actually took (Get-Process / find_app).
    """
    if not shell_enabled():
        return {"ok": False, "error": "shell 已被禁用（强制 GUI 模式）"}
    if not script or not script.strip():
        return {"ok": False, "error": "脚本为空"}
    blocked = safety.guard(script)
    if blocked is not None:
        return blocked

    safe = "".join(c for c in name if c.isalnum() or c in "-_")[:40] or "task"
    detached_dir = SCRIPTS_DIR / "detached"
    try:
        detached_dir.mkdir(parents=True, exist_ok=True)
        _sweep_old(detached_dir)  # bound accumulation: drop stale .ps1 files
        path = detached_dir / f"{safe}-{int(time.time())}-{uuid.uuid4().hex[:6]}.ps1"
        # UTF-8 BOM so PowerShell 5.1 -File reads non-ASCII (Chinese) correctly.
        path.write_text(script, encoding="utf-8-sig")
    except OSError as exc:
        logger.warning("run_detached 写脚本失败: %s", exc)
        return {"ok": False, "error": f"写入脚本失败: {exc}"}

    BUS.publish("action", action="run_detached", name=safe, script=script[:400])
    # Launch via Start-Process -PassThru: it creates a fully independent process
    # that outlives WinPilot, and -File reads the real .ps1 (zero quoting hell).
    # We grab its PID for verification. (Raw Popen+DETACHED_PROCESS killed the
    # child instantly — Start-Process is the reliable detach primitive here.)
    # Pass the path via a $-variable with '' single-quote escaping so an install
    # path containing a quote can't break out of the argument string.
    safe_path = str(path).replace("'", "''")
    launcher = (
        f"$f = '{safe_path}'; "
        f"$p = Start-Process powershell -WindowStyle Hidden -PassThru "
        f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$f; "
        f"$p.Id"
    )
    result = run(launcher, shell="powershell", timeout_s=15)
    if not result.get("ok"):
        return {"ok": False,
                "error": (result.get("error") or result.get("stderr")
                          or result.get("stdout") or "后台进程启动失败（无输出）"),
                "script_path": str(path)}
    pid_text = (result.get("stdout") or "").strip().splitlines()
    pid = int(pid_text[-1]) if pid_text and pid_text[-1].strip().isdigit() else None
    logger.info("已启动分离进程 PID=%s 脚本=%s", pid, path.name)
    return {"ok": True, "pid": pid, "script_path": str(path),
            "note": "已作为独立后台进程启动，不随 WinPilot 退出而结束。"
                    "可用 find_app('powershell') 或 Get-Process 验证 PID 存活。"}
